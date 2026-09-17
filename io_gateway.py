#!/usr/bin/env python3
"""I/O Delegation V1: install, inspect and verify the context gateway."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
SKILL_SOURCE = ROOT / 'skills' / 'io-delegation'
SERVER_NAME = 'io_context'
STATE_VERSION = 1
USER_ROOT = Path(os.environ.get('IO_DELEGATION_HOME', str(Path.home() / '.io-delegation'))).expanduser()
PROJECTS = USER_ROOT / 'projects'
AUDITS = USER_ROOT / 'audits'
CONFIGS = USER_ROOT / 'configs'
BACKUPS = USER_ROOT / 'backups'

AGENT_DIR = {
    'codex': Path('.agents/skills/io-delegation'),
    'cursor': Path('.agents/skills/io-delegation'),
    'claude-code': Path('.claude/skills/io-delegation'),
}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def project_id(root):
    return hashlib.sha256(str(Path(root).resolve()).encode('utf-8')).hexdigest()[:12]


def state_path(root):
    return PROJECTS / f'{project_id(root)}.json'


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.io-delegation-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path, default=None):
    if not Path(path).is_file():
        return default
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


SAFE_ROOT_EXTS = {
    '.py','.js','.jsx','.ts','.tsx','.json','.toml','.yaml','.yml','.md',
    '.html','.css','.scss','.go','.rs','.java','.kt','.rb','.php','.cs',
    '.cpp','.c','.h','.hpp','.sh','.ps1','.sql','.vue','.svelte'
}
EXCLUDED_TOP = {
    '.git','.agents','.claude','.cursor','.io-delegation-hooks','.io-delegation',
    'node_modules','.venv','venv','dist','build','coverage','benchmark-output',
    '__pycache__','.next','.nuxt','.cache'
}
SENSITIVE_NAMES = {'.env','.npmrc','.pypirc'}


def detect_agents(root):
    found=[]
    if shutil.which('codex') or (root/'.codex').exists(): found.append('codex')
    if shutil.which('claude') or (root/'.claude').exists(): found.append('claude-code')
    if shutil.which('agent') or shutil.which('cursor') or (root/'.cursor').exists(): found.append('cursor')
    return found


def auto_scope(root):
    prefixes=[]; files=[]
    for item in sorted(root.iterdir(), key=lambda p:p.name.lower()):
        low=item.name.lower()
        if low in EXCLUDED_TOP or low.startswith('.'):
            continue
        if item.is_dir() and not item.is_symlink():
            prefixes.append(item.name)
        elif item.is_file() and item.suffix.lower() in SAFE_ROOT_EXTS and low not in SENSITIVE_NAMES:
            files.append(item.name)
    if not prefixes and not files:
        raise ValueError('No safe source scope detected; pass --allow-prefix or --allow-file')
    return prefixes, files


def tree_hash(folder):
    h=hashlib.sha256()
    for path in sorted(p for p in Path(folder).rglob('*') if p.is_file()):
        rel=path.relative_to(folder).as_posix()
        if '__pycache__' in path.parts or path.suffix=='.pyc': continue
        h.update(rel.encode());h.update(path.read_bytes())
    return h.hexdigest()


def install_skill(root, agent, update=False):
    dest=root/AGENT_DIR[agent]
    if dest.exists():
        if tree_hash(dest)==tree_hash(SKILL_SOURCE):
            return dest, 'unchanged'
        if not update:
            raise ValueError(f'Existing {agent} skill differs; rerun with --update after review')
        backup=BACKUPS/project_id(root)/(agent+'-'+time.strftime('%Y%m%dT%H%M%S'))
        backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(dest,backup)
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(SKILL_SOURCE,dest,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    return dest, 'installed'


def backup_file(root, path):
    if not path.exists(): return None
    folder=BACKUPS/project_id(root)/'config'
    folder.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    target=folder/f'{path.name}.{digest}.bak'
    if not target.exists(): target.write_bytes(path.read_bytes())
    return target


def external_file(root, value, label):
    path=Path(value).expanduser().resolve(strict=True)
    if root==path or root in path.parents:
        raise ValueError(f'{label} must live outside the project')
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'{label} must be a regular non-symlink file')
    return path


def make_router_config(root, mode, env_name, supplied=None):
    scripts=str(SKILL_SOURCE/'scripts');sys.path.insert(0,scripts)
    import decision_router
    if supplied:
        path=external_file(root,supplied,'Router config')
        decision_router.load_config(str(path))
        return path
    enabled=(mode=='on' or (mode=='auto' and bool(os.environ.get(env_name))))
    if not enabled: return None
    CONFIGS.mkdir(parents=True,exist_ok=True)
    path=CONFIGS/f'{project_id(root)}.router.json'
    data=dict(version=1,approved=True,provider='typesafe',model='jev-latest',
              api_key_env=env_name,timeout_seconds=10,min_confidence=.75)
    atomic_write(path,encoded(data));decision_router.load_config(str(path))
    return path


def validate_worker(root, supplied):
    if not supplied: return None
    path=external_file(root,supplied,'Worker config')
    sys.path.insert(0,str(SKILL_SOURCE/'scripts'))
    import io_delegate
    io_delegate.load_config(str(path))
    return path


def server_args(skill_root, state):
    args=['-I',str(skill_root/'scripts/context_mcp.py'),
          '--root',state['project'],'--audit-root',state['audit_root']]
    for value in state['allow_prefixes']: args += ['--allow-prefix',value]
    for value in state['allow_files']: args += ['--allow-file',value]
    if state.get('worker_config'): args += ['--config',state['worker_config']]
    if state.get('router_config'): args += ['--router-config',state['router_config']]
    return args


def env_names(state):
    names=[];sys.path.insert(0,str(SKILL_SOURCE/'scripts'))
    if state.get('worker_config'):
        import io_delegate
        cfg=io_delegate.load_config(state['worker_config'])
        if cfg.get('api_key_env'): names.append(cfg['api_key_env'])
    if state.get('router_config'):
        import decision_router
        cfg=decision_router.load_config(state['router_config'])
        names.append(cfg['api_key_env'])
    return list(dict.fromkeys(names))


def toml_value(value):
    return json.dumps(value,ensure_ascii=True,separators=(',',':'))


CODEX_BEGIN='# BEGIN io-delegation managed io_context'
CODEX_END='# END io-delegation managed io_context'


def register_codex(root, skill_root, state, update=False):
    path=root/'.codex/config.toml';path.parent.mkdir(parents=True,exist_ok=True)
    current=path.read_text(encoding='utf-8-sig') if path.exists() else ''
    if '[mcp_servers.io_context]' in current and CODEX_BEGIN not in current:
        raise ValueError('Codex already has an unmanaged io_context MCP server')
    args=server_args(skill_root,state);envs=env_names(state)
    block=[CODEX_BEGIN,'[mcp_servers.io_context]',
           f'command = {toml_value(sys.executable)}',f'args = {toml_value(args)}',
           'enabled = true','required = false','startup_timeout_sec = 20',
           'tool_timeout_sec = 185',f'enabled_tools = {toml_value(["search","extract","query"])}']
    if envs: block.append(f'env_vars = {toml_value(envs)}')
    block.append(CODEX_END);managed='\n'.join(block)
    if CODEX_BEGIN in current:
        before,rest=current.split(CODEX_BEGIN,1)
        if CODEX_END not in rest: raise ValueError('Malformed managed Codex block')
        _,after=rest.split(CODEX_END,1);updated=before.rstrip()+"\n\n"+managed+after
    else:
        updated=current.rstrip()+('\n\n' if current.strip() else '')+managed+'\n'
    if updated!=current:
        backup_file(root,path);atomic_write(path,updated.encode('utf-8'))
    return path


def json_mcp_entry(skill_root,state,agent):
    env={}
    for name in env_names(state):
        env[name]=(f'${{{name}}}' if agent=='claude-code' else f'${{env:{name}}}')
    entry={'command':sys.executable,'args':server_args(skill_root,state)}
    if agent=='cursor': entry['type']='stdio'
    if env: entry['env']=env
    return entry


def register_json_mcp(root, skill_root, state, agent, update=False):
    path=(root/'.mcp.json' if agent=='claude-code' else root/'.cursor/mcp.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    current=read_json(path,{})
    if not isinstance(current,dict): raise ValueError(f'{path.name} must contain a JSON object')
    servers=current.setdefault('mcpServers',{})
    if not isinstance(servers,dict): raise ValueError('mcpServers must be an object')
    wanted=json_mcp_entry(skill_root,state,agent)
    if SERVER_NAME in servers and servers[SERVER_NAME]!=wanted and not update:
        raise ValueError(f'{agent} already has a different io_context server; use --update after review')
    if servers.get(SERVER_NAME)==wanted: return path
    backup_file(root,path);servers[SERVER_NAME]=wanted
    atomic_write(path,encoded(current));return path


def register_agent(root, agent, skill_root, state, update=False):
    if agent=='codex': return register_codex(root,skill_root,state,update)
    if agent in ('cursor','claude-code'):
        return register_json_mcp(root,skill_root,state,agent,update)
    raise ValueError('Unsupported agent')


def resolve_agents(root, requested):
    if requested:
        values=[]
        for item in requested:
            if item=='all': values.extend(['codex','claude-code','cursor'])
            elif item=='auto': values.extend(detect_agents(root))
            else: values.append(item)
    else:
        values=detect_agents(root)
    result=list(dict.fromkeys(values))
    if not result:
        raise ValueError('No supported agent detected; pass --agent codex, claude-code, cursor, or all')
    return result


def run_guard_setup(root, agents, mode):
    if mode=='off': return []
    rows=[]
    for agent in agents:
        cp=subprocess.run([sys.executable,str(ROOT/'install_hooks.py'),'--agent',agent,
                           '--project',str(root),'--mode',mode],capture_output=True,text=True,
                          encoding='utf-8',errors='replace',timeout=30)
        if cp.returncode:
            raise ValueError(f'Read-guard setup failed for {agent}: {cp.stderr[-400:]}')
        rows.append(json.loads(cp.stdout))
    return rows


def save_state(state):
    path=state_path(state['project']);atomic_write(path,encoded(state));return path


def load_state(root):
    state=read_json(state_path(root))
    if not state or state.get('version')!=STATE_VERSION:
        raise ValueError('I/O Delegation is not set up for this project')
    return state


def command_setup(args):
    root=Path(args.project).expanduser().resolve(strict=True)
    if not root.is_dir(): raise ValueError('Project must be a directory')
    agents=resolve_agents(root,args.agent)
    if args.allow_prefix or args.allow_file:
        prefixes=list(dict.fromkeys(args.allow_prefix or []));files=list(dict.fromkeys(args.allow_file or []))
    else:
        prefixes,files=auto_scope(root)
    audit=AUDITS/project_id(root);audit.mkdir(parents=True,exist_ok=True)
    worker=validate_worker(root,args.worker_config)
    router=make_router_config(root,args.jev,args.typesafe_env,args.router_config)
    state=dict(version=STATE_VERSION,project=str(root),project_id=project_id(root),
               agents=agents,audit_root=str(audit),allow_prefixes=prefixes,allow_files=files,
               worker_config=str(worker) if worker else None,
               router_config=str(router) if router else None,guard_mode=args.guard,
               configured_at=int(time.time()),configs={})
    installed={}
    for agent in agents:
        skill,status=install_skill(root,agent,args.update);installed[agent]=status
        state['configs'][agent]=str(register_agent(root,agent,skill,state,args.update))
    run_guard_setup(root,agents,args.guard)
    path=save_state(state)
    print(f'I/O Delegation ready for {root}')
    print('Agents: '+', '.join(agents))
    print(f'Context scope: {len(prefixes)} folders + {len(files)} root files')
    print('Smart routing: '+('TypeSafe Jev' if router else 'local rules'))
    print('Semantic worker: '+('configured' if worker else 'off'))
    print('Read guard: '+args.guard)
    print(f'State: {path}')
    if not args.no_doctor:
        return command_doctor(argparse.Namespace(project=str(root),json=False,quiet=True))
    return 0


def audit_summary(state):
    path=Path(state['audit_root'])/'context-events.jsonl'
    rows=[]
    if path.is_file():
        for line in path.read_text(encoding='utf-8',errors='replace').splitlines()[-400:]:
            try:
                row=json.loads(line)
                if row.get('event')=='operation_completed': rows.append(row)
            except ValueError: pass
    routes={}
    for row in rows:
        route=row.get('route','unknown');routes[route]=routes.get(route,0)+1
    return dict(operations=len(rows),routes=routes,
                model_calls=sum(int(r.get('model_calls',0) or 0) for r in rows),
                router_calls=sum(int(r.get('router_calls',0) or 0) for r in rows),
                errors=sum(r.get('status') not in ('ok','insufficient_context') for r in rows),
                cache_hits=sum(r.get('cache')=='hit' for r in rows))


def config_present(state,agent):
    path=Path(state['configs'].get(agent,''))
    if not path.is_file(): return False
    text=path.read_text(encoding='utf-8-sig',errors='replace')
    return ('[mcp_servers.io_context]' in text if agent=='codex' else 'io_context' in text)


def status_data(root):
    state=load_state(root);guard=root/'.io-delegation-hooks/policy.json'
    guard_row=read_json(guard,{}) if guard.is_file() else {}
    router_key=None
    if state.get('router_config'):
        sys.path.insert(0,str(SKILL_SOURCE/'scripts'));import decision_router
        router_key=decision_router.load_config(state['router_config'])['api_key_env']
    return dict(project=state['project'],agents={a:config_present(state,a) for a in state['agents']},
                scope=dict(prefixes=state['allow_prefixes'],files=state['allow_files']),
                smart_routing=bool(state.get('router_config')),
                router_key_env=router_key,router_key_present=bool(router_key and os.environ.get(router_key)),
                semantic_worker=bool(state.get('worker_config')),
                guard=guard_row.get('mode',state.get('guard_mode','off')),
                audit=audit_summary(state))


def command_status(args):
    root=Path(args.project).expanduser().resolve(strict=True);data=status_data(root)
    if getattr(args,'json',False):
        print(json.dumps(data,ensure_ascii=False,indent=2));return 0
    print('I/O Delegation')
    print(f"Project       {data['project']}")
    for agent,ok in data['agents'].items(): print(f"{agent:<13} {'configured' if ok else 'missing'}")
    print(f"Context scope {len(data['scope']['prefixes'])} folders + {len(data['scope']['files'])} files")
    print('Jev router    '+('enabled' if data['smart_routing'] else 'off'))
    if data['smart_routing']:
        print(f"Jev key       {'available' if data['router_key_present'] else 'missing'} ({data['router_key_env']})")
    print('Worker        '+('configured' if data['semantic_worker'] else 'off'))
    print('Read guard    '+str(data['guard']))
    a=data['audit'];print(f"Activity      {a['operations']} ops Â· {a['router_calls']} routed Â· {a['model_calls']} worker calls Â· {a['errors']} errors")
    if a['routes']: print('Routes        '+', '.join(f'{k}:{v}' for k,v in sorted(a['routes'].items())))
    return 0


def skill_for_state(root,state):
    for agent in state['agents']:
        path=root/AGENT_DIR[agent]
        if (path/'scripts/context_mcp.py').is_file(): return path
    raise ValueError('Installed skill copy is missing')


def mcp_probe(root,state):
    skill=skill_for_state(root,state);cmd=[sys.executable,*server_args(skill,state)]
    messages=[dict(jsonrpc='2.0',id=1,method='initialize',params=dict(protocolVersion='2025-06-18',capabilities={},clientInfo={'name':'io-delegation-doctor','version':'1'})),
              dict(jsonrpc='2.0',method='notifications/initialized'),
              dict(jsonrpc='2.0',id=2,method='tools/list',params={})]
    raw=''.join(json.dumps(x,separators=(',',':'))+'\n' for x in messages).encode()
    cp=subprocess.run(cmd,input=raw,capture_output=True,timeout=20)
    if cp.returncode:
        raise ValueError('Context MCP failed startup preflight')
    replies=[]
    for line in cp.stdout.splitlines():
        try: replies.append(json.loads(line))
        except ValueError: pass
    listed=next((x for x in replies if x.get('id')==2),None)
    if not listed or 'result' not in listed: raise ValueError('Context MCP did not return tools/list')
    return [x['name'] for x in listed['result']['tools']]


def command_doctor(args):
    root=Path(args.project).expanduser().resolve(strict=True);state=load_state(root)
    checks=[]
    def add(name,status,detail=''): checks.append(dict(name=name,status=status,detail=detail))
    for agent in state['agents']:
        add(f'{agent} config','pass' if config_present(state,agent) else 'fail',state['configs'].get(agent,''))
        add(f'{agent} skill','pass' if (root/AGENT_DIR[agent]/'SKILL.md').is_file() else 'fail')
    add('audit directory','pass' if Path(state['audit_root']).is_dir() else 'fail',state['audit_root'])
    try:
        tools=mcp_probe(root,state);needed={'search','extract','query'}
        add('MCP handshake','pass' if needed<=set(tools) else 'fail',', '.join(tools))
    except Exception as exc:
        add('MCP handshake','fail',str(exc))
    if state.get('router_config'):
        try:
            sys.path.insert(0,str(SKILL_SOURCE/'scripts'));import decision_router
            cfg=decision_router.load_config(state['router_config']);key=cfg['api_key_env']
            add('Jev config','pass',state['router_config'])
            add('Jev key','pass' if os.environ.get(key) else 'warn',key)
        except Exception as exc: add('Jev config','fail',str(exc))
    if state.get('worker_config'):
        try:
            validate_worker(root,state['worker_config']);add('worker config','pass',state['worker_config'])
        except Exception as exc: add('worker config','fail',str(exc))
    guard=root/'.io-delegation-hooks/policy.json'
    if state.get('guard_mode')!='off':
        row=read_json(guard,{}) if guard.is_file() else {}
        add('read guard','pass' if row.get('mode') in ('observe','enforce') else 'fail',str(row.get('mode','missing')))
    if getattr(args,'json',False): print(json.dumps(checks,ensure_ascii=False,indent=2))
    else:
        print('Doctor')
        for row in checks:
            mark={'pass':'âœ“','warn':'!','fail':'âœ—'}[row['status']]
            print(f"{mark} {row['name']}: {row['detail']}".rstrip(': '))
    return 2 if any(x['status']=='fail' for x in checks) else 0


def build_parser():
    p=argparse.ArgumentParser(prog='io-delegation',description='Context gateway for coding agents')
    sub=p.add_subparsers(dest='command',required=True)
    setup=sub.add_parser('setup',help='Install the skill, MCP context gateway and safe defaults')
    setup.add_argument('--project',default='.')
    setup.add_argument('--agent',action='append',choices=['auto','all','codex','claude-code','cursor'])
    setup.add_argument('--allow-prefix',action='append')
    setup.add_argument('--allow-file',action='append')
    setup.add_argument('--worker-config')
    setup.add_argument('--router-config')
    setup.add_argument('--jev',choices=['auto','on','off'],default='auto')
    setup.add_argument('--typesafe-env',default='TYPESAFE_API_KEY')
    setup.add_argument('--guard',choices=['off','observe','enforce'],default='observe')
    setup.add_argument('--update',action='store_true')
    setup.add_argument('--no-doctor',action='store_true')
    setup.set_defaults(func=command_setup)
    for name,func in [('status',command_status),('doctor',command_doctor)]:
        cmd=sub.add_parser(name);cmd.add_argument('--project',default='.')
        cmd.add_argument('--json',action='store_true');cmd.set_defaults(func=func)
    return p


def main(argv=None):
    try:
        args=build_parser().parse_args(argv);return args.func(args)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        print(f'io-delegation: {exc}',file=sys.stderr);return 2


if __name__=='__main__': raise SystemExit(main())
