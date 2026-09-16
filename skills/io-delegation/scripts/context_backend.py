"""v0.4 backend additions without modifying the proven legacy transport.

Optional strict JSON schema is only sent when the operator declares support.
Codex CLI keeps its normal schema/instructions and is not a hard token-limit API.
"""
from __future__ import annotations
import copy
import json
import os
import urllib.request

import io_delegate as delegate
from worker_runtime import reader_schema as legacy_schema, normalize_usage, usage_complete


def reader_schema(bounded=True):
    schema=copy.deepcopy(legacy_schema())
    if not bounded:return schema
    finding=schema['properties']['findings']['items']
    for key,maximum in (('path',64),('symbol',160),('evidence',240),('fact',400)):
        finding['properties'][key].update(minLength=1,maxLength=maximum)
    schema['properties']['findings']['maxItems']=12
    schema['properties']['unknowns'].update(maxItems=5,items=dict(type='string',minLength=1,maxLength=240))
    schema['properties']['read_paths'].update(maxItems=12,items=dict(type='string',minLength=1,maxLength=64))
    return schema


def check_options(cfg):
    if cfg.get('structured_output','none') not in ('none','json_schema'):
        raise ValueError('structured_output must be none or json_schema')
    if cfg.get('structured_output')=='json_schema' and cfg['adapter']!='chat-completions':
        raise ValueError('Explicit structured_output option applies only to compatible chat-completions endpoints')


def invoke(job,cfg,root,record):
    check_options(cfg)
    if cfg.get('structured_output','none')=='none':
        return delegate.invoke(job,cfg,root,record)
    body=dict(model=cfg['model'],messages=job['messages'],stream=False,
              response_format=dict(type='json_schema',json_schema=dict(name='selected_facts',strict=True,schema=reader_schema())))
    body[cfg.get('token_limit_field','max_tokens')]=cfg.get('reader_max_tokens',1200)
    if cfg.get('temperature',0.2) is not None:body['temperature']=cfg.get('temperature',0.2)
    payload=delegate.encode(body)
    if len(payload)>delegate.MAX_REQUEST_BYTES:raise ValueError('Request too large')
    headers={'Content-Type':'application/json'}
    if cfg.get('api_key_env'):headers['Authorization']='Bearer '+os.environ[cfg['api_key_env']]
    request=urllib.request.Request(delegate.endpoint(cfg),data=payload,headers=headers,method='POST')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),delegate.NoRedirect())
    record('worker_dispatched',adapter='chat-completions',model=cfg['model'])
    with opener.open(request,timeout=cfg.get('timeout_seconds',60)) as response:
        raw=response.read(delegate.MAX_RESPONSE_BYTES+1)
    if len(raw)>delegate.MAX_RESPONSE_BYTES:raise ValueError('Response too large')
    result=json.loads(raw)
    usage=normalize_usage(result.get('usage') if isinstance(result,dict) else None)
    record('worker_response',usage=usage,usage_complete=usage_complete(usage))
    choices=result.get('choices') if isinstance(result,dict) else None
    if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):raise ValueError('Invalid response choices')
    first=choices[0];message=first.get('message')
    if first.get('finish_reason')!='stop' or not isinstance(message,dict) or not isinstance(message.get('content'),str):
        raise ValueError('Incomplete, refused or non-text response')
    return message['content'],dict(usage=usage,request_bytes=len(payload))
