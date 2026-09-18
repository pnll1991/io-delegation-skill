#!/usr/bin/env python3
"""I/O Delegation Context Gateway: setup, status, doctor and lifecycle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
SKILL_SOURCE = ROOT / 'skills' / 'io-delegation'
SERVER_NAME = 'io_context'
STATE_VERSION = 2
DEFAULT_JEV_SETUP_MODE = 'off'
DEFAULT_COMPACTION_SETUP_MODE = 'on'
DEFAULT_ORCHESTRATION_SETUP_MODE = 'auto'
COMPACTION_PLUGIN_NAME = 'io-delegation-jev-compaction'
COMPACTION_PLUGIN_REF = 'io-delegation-jev-compaction@io-delegation'
COMPACTION_MARKETPLACE = 'pnll1991/io-delegation-skill'
COMPACTION_DATA_SCOPE = 'conversation_text_and_tool_inputs'
USER_ROOT = Path(os.environ.get('IO_DELEGATION_HOME', str(Path.home()/'.io-delegation'))).expanduser()
HOST_HOME = Path(os.environ.get('IO_DELEGATION_HOST_HOME', str(Path.home()))).expanduser()
MARKER_DIR = Path('.io-delegation')
MARKER_FILE = MARKER_DIR/'project.json'
ID_RE = re.compile(r'^[0-9a-f]{12,32}$')

AGENT_DIR = {
    'codex': Path('.agents/skills/io-delegation'),
    'cursor': Path('.agents/skills/io-delegation'),
    'claude-code': Path('.claude/skills/io-delegation'),
}

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
CODEX_BEGIN = '# BEGIN io-delegation managed io_context'
CODEX_END = '# END io-delegation managed io_context'


def projects_dir(): return USER_ROOT/'projects'
def audits_dir(): return USER_ROOT/'audits'
def configs_dir(): return USER_ROOT/'configs'
def backups_dir(): return USER_ROOT/'backups'
def runtime_skill(): return USER_ROOT/'runtime'/'io-delegation'
def runtime_bootstrap(): return runtime_skill()/'scripts'/'context_bootstrap.py'
def runtime_compaction(): return runtime_skill()/'scripts'/'context_compaction.py'
def codex_config_path():
    home = Path(os.environ.get('CODEX_HOME', str(HOST_HOME/'.codex'))).expanduser()
    return home/'config.toml'
def cursor_config_path(): return HOST_HOME/'.cursor'/'mcp.json'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')


def atomic_write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.io-delegation-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle: handle.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def read_json(path, default=None):
    path = Path(path)
    if not path.is_file(): return default
    return json.loads(path.read_text(encoding='utf-8-sig'))


def file_hash(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def tree_hash(folder):
    h = hashlib.sha256(); folder = Path(folder)
    for path in sorted(p for p in folder.rglob('*') if p.is_file()):
        if '__pycache__' in path.parts or path.suffix == '.pyc': continue
        h.update(path.relative_to(folder).as_posix().encode('utf-8')); h.update(path.read_bytes())
    return h.hexdigest()


def detect_agents(root):
    found=[]
    if shutil.which('codex') or (root/'.agents').exists(): found.append('codex')
    if shutil.which('claude') or (root/'.claude').exists(): found.append('claude-code')
    if shutil.which('agent') or shutil.which('cursor') or (root/'.cursor').exists(): found.append('cursor')
    return found


def auto_scope(root):
    prefixes=[]; files=[]
    for item in sorted(Path(root).iterdir(), key=lambda p:p.name.lower()):
        low=item.name.lower()
        if low in EXCLUDED_TOP or low.startswith('.'): continue
        if item.is_dir() and not item.is_symlink(): prefixes.append(item.name)
        elif item.is_file() and item.suffix.lower() in SAFE_ROOT_EXTS and low not in SENSITIVE_NAMES:
            files.append(item.name)
    if not prefixes and not files:
        raise ValueError('No safe source scope detected; pass --allow-prefix or --allow-file')
    return prefixes, files


def legacy_project_id(root):
    return hashlib.sha256(str(Path(root).resolve()).encode('utf-8')).hexdigest()[:12]


def marker_path(root): return Path(root)/MARKER_FILE


def marker_id(root):
    path = marker_path(root)
    if not path.is_file() or path.is_symlink(): return None
    row = read_json(path, {})
    value = row.get('project_id') if isinstance(row, dict) else None
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError('Invalid .io-delegation/project.json marker')
    return value


def ensure_marker(root, dry_run=False, project_id=None):
    current = marker_id(root)
    if current: return current, 'existing'
    legacy = projects_dir()/f'{legacy_project_id(root)}.json'
    project_id = project_id or (legacy_project_id(root) if legacy.is_file() else uuid.uuid4().hex[:16])
    if not ID_RE.fullmatch(project_id): raise ValueError('Invalid project id')
    if not dry_run:
        folder = Path(root)/MARKER_DIR; folder.mkdir(parents=True, exist_ok=True)
        atomic_write(folder/'.gitignore', b'*\n!.gitignore\n')
        atomic_write(folder/'project.json', encoded({'version':1,'project_id':project_id}))
    return project_id, 'create'


def state_path_id(project_id): return projects_dir()/f'{project_id}.json'


def state_path(root):
    value = marker_id(root) or legacy_project_id(root)
    return state_path_id(value)


def load_state(root):
    root = Path(root).resolve(strict=True)
    project_id = marker_id(root)
    path = state_path_id(project_id) if project_id else projects_dir()/f'{legacy_project_id(root)}.json'
    state = read_json(path)
    if not isinstance(state, dict) or state.get('version') not in (1, STATE_VERSION):
        raise ValueError('I/O Delegation is not set up for this project')
    state = dict(state)
    state.setdefault('project_id', project_id or legacy_project_id(root))
    state['_state_path'] = str(path)
    state['_current_project'] = str(root)
    return state


def save_state(state):
    row = {k:v for k,v in state.items() if not k.startswith('_')}
    row['version'] = STATE_VERSION
    path = state_path_id(row['project_id']); atomic_write(path, encoded(row)); return path


def install_runtime(dry_run=False):
    dest = runtime_skill(); existed_before = dest.exists()
    source_hash = tree_hash(SKILL_SOURCE)
    if dest.is_dir() and tree_hash(dest) == source_hash:
        return dest, 'unchanged'
    if dry_run:
        return dest, 'install' if not dest.exists() else 'update'
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = dest.parent/f'.stage-{uuid.uuid4().hex[:8]}'
    shutil.copytree(SKILL_SOURCE, stage, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    if dest.exists():
        old = backups_dir()/'runtime'/time.strftime('%Y%m%dT%H%M%S')
        old.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(old))
    shutil.move(str(stage), str(dest))
    return dest, 'updated' if existed_before else 'installed'


def install_skill(root, agent, update=False, dry_run=False):
    dest = Path(root)/AGENT_DIR[agent]
    if dest.exists():
        if tree_hash(dest) == tree_hash(SKILL_SOURCE): return dest, 'unchanged'
        if not update: raise ValueError(f'Existing {agent} skill differs; rerun with --update after review')
        if dry_run: return dest, 'update'
        backup = backups_dir()/'skills'/marker_id(root)/(agent+'-'+time.strftime('%Y%m%dT%H%M%S'))
        backup.parent.mkdir(parents=True, exist_ok=True); shutil.copytree(dest, backup); shutil.rmtree(dest)
    if dry_run: return dest, 'install'
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL_SOURCE, dest, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    return dest, 'installed'


def backup_snapshot(path, label):
    path = Path(path); folder = backups_dir()/'configs'; folder.mkdir(parents=True, exist_ok=True)
    ident = time.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    backup = folder/f'{ident}.bak'; meta = folder/f'{ident}.json'
    existed = path.is_file(); before = file_hash(path)
    if existed: backup.write_bytes(path.read_bytes())
    atomic_write(meta, encoded({'id':ident,'label':label,'target':str(path),'existed':existed,
                                'backup':str(backup) if existed else None,'before_sha256':before,
                                'after_sha256':None,'created_at':int(time.time())}))
    return meta


def finalize_backup(meta_path, target):
    row = read_json(meta_path); row['after_sha256'] = file_hash(target); atomic_write(meta_path, encoded(row))


def list_backups():
    folder = backups_dir()/'configs'
    rows=[]
    if not folder.is_dir(): return rows
    for path in sorted(folder.glob('*.json'), reverse=True):
        try:
            row=read_json(path); row['meta_path']=str(path); rows.append(row)
        except Exception: pass
    return rows


def restore_backup(ident, force=False):
    path = backups_dir()/'configs'/f'{ident}.json'
    row = read_json(path)
    if not isinstance(row, dict) or row.get('id') != ident:
        raise ValueError('Unknown backup id')
    target = Path(row['target'])
    current = file_hash(target)
    if not force and current != row.get('after_sha256'):
        raise ValueError('Target changed after I/O Delegation wrote it; use --force only after review')
    if row.get('existed'):
        backup = Path(row['backup'])
        if not backup.is_file() or file_hash(backup) != row.get('before_sha256'):
            raise ValueError('Backup file is missing or changed')
        atomic_write(target, backup.read_bytes())
    elif target.exists():
        target.unlink()
    row['restored_at']=int(time.time()); atomic_write(path, encoded(row))
    return target


def external_file(root, value, label):
    path=Path(value).expanduser().resolve(strict=True)
    root=Path(root).resolve(strict=True)
    if root==path or root in path.parents: raise ValueError(f'{label} must live outside the project')
    if path.is_symlink() or not path.is_file(): raise ValueError(f'{label} must be a regular non-symlink file')
    return path


def remove_generated_config(path):
    if not path:
        return False
    candidate=Path(path)
    try:
        candidate.resolve().relative_to(configs_dir().resolve())
    except (ValueError,OSError):
        return False
    if candidate.is_file():
        candidate.unlink()
        return True
    return False


def router_enabled(mode, env_name):
    return mode == 'on' or (mode == 'auto' and bool(os.environ.get(env_name)))


def validate_router(root, supplied=None):
    if not supplied: return None
    path=external_file(root,supplied,'Router config')
    sys.path.insert(0,str(SKILL_SOURCE/'scripts')); import decision_router
    decision_router.load_config(str(path)); return path


def make_router_config(root, project_id, mode, env_name, supplied=None, dry_run=False):
    external = validate_router(root, supplied) if supplied else None
    if external: return external, False
    if not router_enabled(mode, env_name): return None, False
    path=configs_dir()/f'{project_id}.router.json'
    if dry_run: return path, True
    data={'version':1,'approved':True,'provider':'typesafe','model':'jev-latest',
          'api_key_env':env_name,'timeout_seconds':10,'min_confidence':.75}
    atomic_write(path, encoded(data))
    sys.path.insert(0,str(SKILL_SOURCE/'scripts')); import decision_router
    decision_router.load_config(str(path)); return path, True



def make_orchestrator_config(project_id, mode, env_name, worker, dry_run=False):
    if mode not in ('auto','on','off'):
        raise ValueError('Unknown orchestration mode')
    if mode == 'on' and not worker:
        raise ValueError('--orchestration on requires an approved --worker-config')
    if mode == 'off' or not worker:
        return None, False
    path=configs_dir()/f'{project_id}.orchestrator.json'
    if dry_run:
        return path, True
    data={'version':1,'approved':True,'provider':'typesafe','model':'jev-latest',
          'api_key_env':env_name,'timeout_seconds':10,'min_confidence':.75,
          'compute_policy':{
              'cheap_sufficient_min':.78,
              'risk_high_max':.30,
              'uncertainty_high_max':.35,
              'reasoning_required_max':.40,
              'max_cheap_output_tokens':900,
          }}
    atomic_write(path, encoded(data))
    sys.path.insert(0,str(SKILL_SOURCE/'scripts')); import context_orchestrator
    context_orchestrator.load_config(str(path))
    return path, True


def compaction_policy_path(root):
    return Path(root)/MARKER_DIR/'compaction.json'


def compaction_policy_data(env_name, agents=None):
    return {
        'version':1,
        'enabled':True,
        'provider':'typesafe',
        'approved_data_scope':COMPACTION_DATA_SCOPE,
        'api_key_env':env_name,
        'model':'jev-latest',
        'hosts':list(dict.fromkeys(agents or [])),
        'keep_threshold':0.5,
        'preserve_recent_messages':6,
        'compact_at_percent':60,
        'min_reduction_ratio':0.25,
        'max_state_tokens':25000,
        'max_request_tokens':30000,
        'truncate_head_chars':300,
        'max_rehydrate_chars':24000,
        'retain_session_cache':False,
        'upstream_commit':'e3f262a7f4d42bd8dd32ced30d26176f7cb545b0',
    }


def sync_compaction_policy(root, mode, env_name, agents=None, dry_run=False):
    path=compaction_policy_path(root)
    if mode != 'on':
        if not path.exists(): return path,'unchanged'
        if dry_run: return path,'remove'
        path.unlink(); return path,'removed'
    data=encoded(compaction_policy_data(env_name,agents))
    if path.is_file() and path.read_bytes()==data: return path,'unchanged'
    if dry_run: return path,'write'
    atomic_write(path,data); return path,'written'


def claude_settings_path():
    return HOST_HOME/'.claude'/'settings.json'


def claude_function_hooks_enabled():
    if os.environ.get('CLAUDE_CODE_ENABLE_FUNCTION_HOOKS') == '1': return True
    row=read_json(claude_settings_path(),{})
    env=row.get('env',{}) if isinstance(row,dict) else {}
    return isinstance(env,dict) and str(env.get('CLAUDE_CODE_ENABLE_FUNCTION_HOOKS','')) == '1'


def ensure_claude_function_hooks_flag(dry_run=False):
    if claude_function_hooks_enabled(): return 'unchanged'
    path=claude_settings_path(); current=read_json(path,{})
    if not isinstance(current,dict): raise ValueError('Claude settings.json must contain an object')
    env=current.get('env')
    if env is None: env={}
    if not isinstance(env,dict): raise ValueError('Claude settings env must contain an object')
    existing=env.get('CLAUDE_CODE_ENABLE_FUNCTION_HOOKS')
    if existing not in (None,'1',1,True):
        raise ValueError('Claude function hooks flag has a conflicting value')
    new=json.loads(json.dumps(current)); new.setdefault('env',{})['CLAUDE_CODE_ENABLE_FUNCTION_HOOKS']='1'
    if dry_run: return 'write'
    snap=backup_snapshot(path,'claude-function-hooks'); atomic_write(path,encoded(new)); finalize_backup(snap,path)
    return 'written'


def claude_compaction_installed():
    exe=claude_cli()
    if not exe: return False
    cp=subprocess.run([exe,'plugin','list'],capture_output=True,text=True,encoding='utf-8',
                      errors='replace',timeout=30)
    if cp.returncode: return False
    text=(cp.stdout or '')+(cp.stderr or '')
    return COMPACTION_PLUGIN_NAME in text


def ensure_claude_compaction_plugin(dry_run=False):
    exe=claude_cli()
    if not exe: return 'unavailable'
    if claude_compaction_installed(): return 'unchanged'
    if dry_run: return 'install'
    def call(argv,label,allow_already=False):
        cp=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        text=(cp.stdout or '')+(cp.stderr or '')
        if cp.returncode and not (allow_already and 'already' in text.lower()):
            raise ValueError(label+' failed: '+text[-400:])
    call([exe,'plugin','marketplace','add',COMPACTION_MARKETPLACE],
         'Claude plugin marketplace registration',allow_already=True)
    call([exe,'plugin','install',COMPACTION_PLUGIN_REF],'Claude compaction plugin installation')
    return 'installed'


def validate_worker(root, supplied):
    if not supplied: return None
    path=external_file(root,supplied,'Worker config')
    sys.path.insert(0,str(SKILL_SOURCE/'scripts')); import io_delegate
    io_delegate.load_config(str(path)); return path


def worker_auto_dispatch(path):
    if not path: return False
    row=read_json(path,{})
    return isinstance(row,dict) and row.get('context_auto_dispatch') is True


def env_names_for_state(state):
    explicit=state.get('credential_env_names')
    if isinstance(explicit,list): return list(dict.fromkeys(x for x in explicit if isinstance(x,str) and x))
    names=[]; sys.path.insert(0,str(SKILL_SOURCE/'scripts'))
    if state.get('worker_config'):
        import io_delegate
        cfg=io_delegate.load_config(state['worker_config'])
        if cfg.get('api_key_env'): names.append(cfg['api_key_env'])
    if state.get('router_config'):
        import decision_router
        cfg=decision_router.load_config(state['router_config'])
        names.append(cfg['api_key_env'])
    if state.get('orchestrator_config'):
        import context_orchestrator
        cfg=context_orchestrator.load_config(state['orchestrator_config'])
        names.append(cfg['api_key_env'])
    if state.get('compaction_mode') == 'on':
        name=state.get('compaction_api_key_env','TYPESAFE_API_KEY')
        if isinstance(name,str) and name: names.append(name)
    return list(dict.fromkeys(names))


def all_states(exclude_id=None, extra=None):
    rows=[]; folder=projects_dir()
    if folder.is_dir():
        for path in folder.glob('*.json'):
            try:
                row=read_json(path)
                if not isinstance(row,dict): continue
                pid=row.get('project_id') or path.stem
                if pid == exclude_id: continue
                row=dict(row); row['project_id']=pid; rows.append(row)
            except Exception: pass
    if extra is not None:
        rows=[r for r in rows if r.get('project_id')!=extra.get('project_id')]; rows.append(extra)
    return rows


def union_env_names(states):
    result=[]
    if os.environ.get('IO_DELEGATION_HOME'): result.append('IO_DELEGATION_HOME')
    for state in states:
        try: result.extend(env_names_for_state(state))
        except Exception: pass
    return list(dict.fromkeys(result))


def agents_in_states(states):
    return {a for state in states for a in state.get('agents',[])}


def runtime_command():
    return str(Path(sys.executable).resolve()), str(runtime_bootstrap().resolve())


def toml_value(value):
    return json.dumps(value, ensure_ascii=True, separators=(',',':'))


def codex_block(states):
    command, bootstrap = runtime_command(); envs=union_env_names(states)
    rows=[CODEX_BEGIN,'[mcp_servers.io_context]',f'command = {toml_value(command)}',
          f'args = {toml_value([bootstrap,"--host","codex"])}','enabled = true','required = false',
          'startup_timeout_sec = 20','tool_timeout_sec = 185',
          f'enabled_tools = {toml_value(["search","extract","query"])}']
    if envs: rows.append(f'env_vars = {toml_value(envs)}')
    rows.append(CODEX_END)
    return '\n'.join(rows)


def update_codex_global(states, dry_run=False):
    path=codex_config_path(); current=path.read_text(encoding='utf-8-sig') if path.is_file() else ''
    need='codex' in agents_in_states(states)
    if '[mcp_servers.io_context]' in current and CODEX_BEGIN not in current:
        raise ValueError('Codex already has an unmanaged io_context MCP server')
    if CODEX_BEGIN in current:
        before,rest=current.split(CODEX_BEGIN,1)
        if CODEX_END not in rest: raise ValueError('Malformed managed Codex MCP block')
        _,after=rest.split(CODEX_END,1)
        base=(before.rstrip()+('\n\n' if before.rstrip() and after.lstrip() else '')+after.lstrip()).rstrip()
    else:
        base=current.rstrip()
    updated=(base+('\n\n' if base else '')+codex_block(states)+'\n') if need else (base+'\n' if base else '')
    if updated==current: return path, 'unchanged'
    if dry_run: return path, 'write' if need else 'remove'
    snap=backup_snapshot(path,'codex-global-mcp'); atomic_write(path,updated.encode('utf-8')); finalize_backup(snap,path)
    return path, 'written' if need else 'removed'


def cursor_entry(states):
    command, bootstrap=runtime_command(); envs=union_env_names(states)
    entry={'type':'stdio','command':command,'args':[bootstrap,'--host','cursor']}
    if envs: entry['env']={name:f'${{env:{name}}}' for name in envs}
    return entry


def owned_cursor_entry(value):
    if not isinstance(value,dict): return False
    args=value.get('args')
    return isinstance(args,list) and any(str(x).endswith('context_bootstrap.py') for x in args)


def update_cursor_global(states, dry_run=False, replace=False):
    path=cursor_config_path(); current=read_json(path,{})
    if not isinstance(current,dict): raise ValueError('Cursor global mcp.json must contain an object')
    servers=current.setdefault('mcpServers',{})
    if not isinstance(servers,dict): raise ValueError('Cursor mcpServers must be an object')
    need='cursor' in agents_in_states(states); existing=servers.get(SERVER_NAME)
    if existing is not None and not owned_cursor_entry(existing) and not replace:
        raise ValueError('Cursor already has an unmanaged io_context server; use --replace-existing-mcp after review')
    wanted=cursor_entry(states) if need else None
    if existing==wanted or (not need and existing is None): return path,'unchanged'
    new=json.loads(json.dumps(current))
    target=new.setdefault('mcpServers',{})
    if need: target[SERVER_NAME]=wanted
    else: target.pop(SERVER_NAME,None)
    if dry_run: return path,'write' if need else 'remove'
    snap=backup_snapshot(path,'cursor-global-mcp'); atomic_write(path,encoded(new)); finalize_backup(snap,path)
    return path,'written' if need else 'removed'


def claude_cli():
    return shutil.which('claude')


def claude_server_owned():
    exe=claude_cli()
    if not exe: return False, None
    cp=subprocess.run([exe,'mcp','get',SERVER_NAME],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)
    if cp.returncode: return False, None
    text=(cp.stdout or '')+(cp.stderr or '')
    return 'context_bootstrap.py' in text, text


def update_claude_user(states, dry_run=False, replace=False):
    need='claude-code' in agents_in_states(states); exe=claude_cli()
    if not exe:
        return None, 'unavailable' if need else 'unchanged'
    owned, existing=claude_server_owned()
    if existing is not None and not owned and not replace:
        raise ValueError('Claude Code already has an unmanaged io_context server; use --replace-existing-mcp after review')
    if dry_run:
        return 'claude:user', 'write' if need else ('remove' if owned else 'unchanged')
    if owned:
        subprocess.run([exe,'mcp','remove',SERVER_NAME,'--scope','user'],capture_output=True,text=True,
                       encoding='utf-8',errors='replace',timeout=30)
    if not need:
        return 'claude:user','removed' if owned else 'unchanged'
    command,bootstrap=runtime_command()
    definition=json.dumps({'type':'stdio','command':command,'args':[bootstrap,'--host','claude-code']},separators=(',',':'))
    cp=subprocess.run([exe,'mcp','add-json',SERVER_NAME,definition,'--scope','user'],
                      capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    if cp.returncode:
        raise ValueError('Claude Code user-scope MCP registration failed: '+(cp.stderr or cp.stdout)[-400:])
    return 'claude:user','written'


def sync_global_mcp(states, dry_run=False, replace=False):
    result={}
    result['codex']=update_codex_global(states,dry_run=dry_run)
    result['cursor']=update_cursor_global(states,dry_run=dry_run,replace=replace)
    result['claude-code']=update_claude_user(states,dry_run=dry_run,replace=replace)
    return result


def resolve_agents(root, requested):
    values=[]
    for item in requested or ['auto']:
        if item=='all': values.extend(['codex','claude-code','cursor'])
        elif item=='auto': values.extend(detect_agents(root))
        else: values.append(item)
    result=list(dict.fromkeys(values))
    if not result: raise ValueError('No supported agent detected; pass --agent codex, claude-code, cursor, or all')
    return result

HOOK_CONFIG = {'claude-code':'.claude/settings.json','codex':'.codex/hooks.json','cursor':'.cursor/hooks.json'}


def git_tracked(root, relative):
    if not (Path(root)/'.git').exists() and not shutil.which('git'): return False
    cp=subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch','--',str(relative)],
                      capture_output=True,text=True,timeout=10)
    return cp.returncode==0


def run_guard_setup(root, agents, mode, *, dry_run=False, allow_tracked=False):
    if mode=='off': return []
    rows=[]
    for agent in agents:
        rel=HOOK_CONFIG[agent]
        if git_tracked(root,rel) and not allow_tracked:
            raise ValueError(f'{rel} is tracked by git; use --allow-tracked-config only after reviewing the hook diff')
        if dry_run:
            rows.append({'agent':agent,'status':'would_install','mode':mode,'config':str(Path(root)/rel)})
            continue
        cp=subprocess.run([sys.executable,str(ROOT/'install_hooks.py'),'--agent',agent,
                           '--project',str(root),'--mode',mode],capture_output=True,text=True,
                          encoding='utf-8',errors='replace',timeout=30)
        if cp.returncode: raise ValueError(f'Read-guard setup failed for {agent}: '+(cp.stderr or cp.stdout)[-400:])
        rows.append(json.loads(cp.stdout))
    return rows


def remove_guard(root, agents, *, dry_run=False, allow_tracked=False):
    rows=[]
    for agent in agents:
        record=Path(root)/'.io-delegation-hooks'/f'{agent}.installation.json'
        if not record.is_file(): continue
        rel=HOOK_CONFIG[agent]
        if git_tracked(root,rel) and not allow_tracked:
            raise ValueError(f'{rel} is tracked by git; use --allow-tracked-config after reviewing removal')
        if dry_run:
            rows.append({'agent':agent,'status':'would_remove','config':str(Path(root)/rel)}); continue
        cp=subprocess.run([sys.executable,str(ROOT/'install_hooks.py'),'--agent',agent,
                           '--project',str(root),'--remove'],capture_output=True,text=True,
                          encoding='utf-8',errors='replace',timeout=30)
        if cp.returncode: raise ValueError(f'Read-guard removal failed for {agent}: '+(cp.stderr or cp.stdout)[-400:])
        rows.append(json.loads(cp.stdout))
    return rows



def compaction_hook_record(root, agent):
    return Path(root)/'.io-delegation-hooks'/f'{agent}.compaction.json'


def compaction_hook_installed(root, agent):
    if agent == 'claude-code':
        return claude_compaction_installed() and claude_function_hooks_enabled()
    record=read_json(compaction_hook_record(root,agent),{})
    config=read_json(Path(root)/HOOK_CONFIG[agent],{})
    if not isinstance(record,dict) or not isinstance(config,dict): return False
    hooks=config.get('hooks',{})
    if not isinstance(hooks,dict): return False
    entries=record.get('entries',[])
    if not isinstance(entries,list) or not entries: return False
    for row in entries:
        if not isinstance(row,dict): return False
        event=row.get('event'); entry=row.get('entry')
        values=hooks.get(event,[])
        if not isinstance(values,list) or entry not in values: return False
    return True


def run_compaction_hook_setup(root, agents, mode, *, dry_run=False, allow_tracked=False):
    rows=[]
    for agent in agents:
        if agent == 'claude-code': continue
        record=compaction_hook_record(root,agent)
        should_install=mode=='on'
        if not should_install and not record.is_file(): continue
        rel=HOOK_CONFIG[agent]
        if git_tracked(root,rel) and not allow_tracked:
            raise ValueError(f'{rel} is tracked by git; use --allow-tracked-config only after reviewing the compaction hook diff')
        argv=[sys.executable,str(ROOT/'install_compaction_hooks.py'),'--agent',agent,
              '--project',str(root),'--runtime',str(runtime_compaction()),
              '--python',str(Path(sys.executable).resolve())]
        if dry_run: argv.append('--dry-run')
        if not should_install: argv.append('--remove')
        cp=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8',
                          errors='replace',timeout=45)
        if cp.returncode:
            raise ValueError(f'Compaction hook setup failed for {agent}: '+(cp.stderr or cp.stdout)[-400:])
        rows.append(json.loads(cp.stdout))
    return rows


def configured_compaction_hosts(state):
    if not state or state.get('compaction_mode') != 'on': return []
    hosts=state.get('compaction_hosts')
    if isinstance(hosts,list) and hosts: return list(dict.fromkeys(hosts))
    return list(dict.fromkeys(state.get('agents',[])))


def sync_compaction_hook_setup(root, agents, previous_hosts, mode, *, dry_run=False, allow_tracked=False):
    active=set(agents if mode=='on' else [])
    known=list(dict.fromkeys(list(previous_hosts or [])+list(agents or [])))
    rows=[]
    for agent in known:
        rows.extend(run_compaction_hook_setup(
            root,[agent],'on' if agent in active else 'off',
            dry_run=dry_run,allow_tracked=allow_tracked))
    return rows


def optional_state(root):
    try: return load_state(root)
    except ValueError: return None


def credential_names(worker, router, generated_router_env=None, compaction_env=None,
                     orchestrator=None, generated_orchestrator_env=None):
    names=[]; sys.path.insert(0,str(SKILL_SOURCE/'scripts'))
    if worker:
        import io_delegate
        cfg=io_delegate.load_config(str(worker))
        if cfg.get('api_key_env'): names.append(cfg['api_key_env'])
    if router and Path(router).is_file():
        import decision_router
        names.append(decision_router.load_config(str(router))['api_key_env'])
    elif generated_router_env:
        names.append(generated_router_env)
    if compaction_env: names.append(compaction_env)
    if orchestrator and Path(orchestrator).is_file():
        import context_orchestrator
        names.append(context_orchestrator.load_config(str(orchestrator))['api_key_env'])
    elif generated_orchestrator_env:
        names.append(generated_orchestrator_env)
    return list(dict.fromkeys(names))


def compaction_setup_mode(args,existing):
    if args.compaction is not None:
        return args.compaction
    if existing:
        preference=existing.get('compaction_preference')
        if preference in ('on','off'):
            return preference
    return DEFAULT_COMPACTION_SETUP_MODE


def orchestration_setup_mode(args,existing):
    if args.orchestration is not None:
        return args.orchestration
    if existing:
        preference=existing.get('orchestration_preference')
        if preference in ('auto','on','off'):
            return preference
    return DEFAULT_ORCHESTRATION_SETUP_MODE


def setup_choices(root,args,existing):
    agents=(resolve_agents(root,args.agent) if args.agent else
            list(existing.get('agents',[])) if existing else resolve_agents(root,None))
    if args.allow_prefix or args.allow_file:
        prefixes=list(dict.fromkeys(args.allow_prefix or [])); files=list(dict.fromkeys(args.allow_file or []))
    elif existing:
        prefixes=list(existing.get('allow_prefixes',[])); files=list(existing.get('allow_files',[]))
    else:
        prefixes,files=auto_scope(root)
    guard=(args.guard if args.guard is not None else existing.get('guard_mode','off') if existing else 'off')
    activation=(args.activation if args.activation is not None else existing.get('activation_mode','auto') if existing else 'auto')
    worker=None if args.no_worker else args.worker_config or (existing.get('worker_config') if existing else None)
    if args.router_config:
        router_mode='on'; router_source=args.router_config
    elif args.jev is None and existing and existing.get('router_config'):
        router_mode='on'; router_source=existing['router_config']
    else:
        router_mode=args.jev or DEFAULT_JEV_SETUP_MODE; router_source=None
    compaction=compaction_setup_mode(args,existing)
    orchestration=orchestration_setup_mode(args,existing)
    model_preset=(args.model_preset if args.model_preset is not None else
                  existing.get('model_policy_preset','balanced') if existing else 'balanced')
    return agents,prefixes,files,guard,activation,worker,router_mode,router_source,compaction,orchestration,model_preset


def print_setup_plan(root,state,actions,guard_rows,compaction_rows,json_mode=False):
    dispatch=worker_auto_dispatch(state.get('worker_config'))
    plan={'project':str(root),'project_id':state['project_id'],'agents':state['agents'],
          'scope':{'prefixes':state['allow_prefixes'],'files':state['allow_files']},
          'smart_routing':bool(state.get('router_config')),'semantic_worker':bool(state.get('worker_config')),
          'compute_orchestration':bool(state.get('orchestrator_config')),
          'orchestration_preference':state.get('orchestration_preference','auto'),
          'model_policy_preset':state.get('model_policy_preset','balanced'),
          'jev_compaction':state.get('compaction_mode')=='on',
          'compaction_preference':state.get('compaction_preference',state.get('compaction_mode','on')),
          'worker_auto_dispatch':dispatch,
          'guard':state['guard_mode'],'activation':state.get('activation_mode','auto'),
          'actions':actions,'guard_actions':guard_rows,'compaction_actions':compaction_rows,'writes':False}
    if json_mode:
        print(json.dumps(plan,ensure_ascii=False,indent=2)); return
    print('I/O Delegation setup preview (no files changed)')
    print('Project: '+str(root)); print('Agents: '+', '.join(state['agents']))
    print(f"Scope: {len(state['allow_prefixes'])} folders + {len(state['allow_files'])} files")
    print('Jev: '+('on' if state.get('router_config') else 'off'))
    print('Compute orchestration: '+('host-aware / '+state.get('model_policy_preset','balanced') if state.get('orchestrator_config') else 'off'))
    print('Compaction: '+('automatic (project-scoped: '+', '.join(state.get('compaction_hosts',[]))+')' if state.get('compaction_mode')=='on' else 'off'))
    if state.get('worker_config'):
        print('Worker: configured; experimental auto-dispatch '+('on' if dispatch else 'off'))
    else:
        print('Worker: off')
    print('Read guard: '+state['guard_mode'])
    print('Activation: '+state.get('activation_mode','auto'))
    for name,value in actions.items(): print(f'- {name}: {value}')


def command_setup(args):
    root=Path(args.project).expanduser().resolve(strict=True)
    if not root.is_dir(): raise ValueError('Project must be a directory')
    existing=optional_state(root)
    agents,prefixes,files,guard,activation,worker_value,router_mode,router_source,compaction,orchestration,model_preset=setup_choices(root,args,existing)
    old_compaction_hosts=configured_compaction_hosts(existing)

    # Phase 1: resolve and validate the complete plan without writing anything.
    project_id,marker_action=ensure_marker(root,dry_run=True)
    _,runtime_action=install_runtime(dry_run=True)
    worker=validate_worker(root,worker_value) if worker_value else None
    router_plan,router_generated=make_router_config(root,project_id,router_mode,args.typesafe_env,
                                                    router_source,dry_run=True)
    if existing and args.jev is None and not args.router_config and router_plan and str(router_plan)==existing.get('router_config'):
        router_generated=bool(existing.get('router_generated'))
    orchestrator_plan,orchestrator_generated=make_orchestrator_config(
        project_id,orchestration,args.typesafe_env,worker,dry_run=True)
    compaction_policy,compaction_policy_action=sync_compaction_policy(
        root,compaction,args.typesafe_env,agents,dry_run=True)
    claude_compaction=compaction=='on' and 'claude-code' in agents
    compaction_plugin_action=ensure_claude_compaction_plugin(dry_run=True) if claude_compaction else 'off'
    function_hooks_action=ensure_claude_function_hooks_flag(dry_run=True) if claude_compaction else 'off'
    credentials=credential_names(
        worker,router_plan,args.typesafe_env if router_generated else None,
        args.typesafe_env if compaction=='on' else None,
        orchestrator_plan,args.typesafe_env if orchestrator_generated else None)
    skill_actions={}
    for agent in agents:
        _,skill_actions[agent]=install_skill(root,agent,args.update,dry_run=True)
    audit=audits_dir()/project_id
    state={'version':STATE_VERSION,'project':str(root),'project_id':project_id,'agents':agents,
           'audit_root':str(audit),'allow_prefixes':prefixes,'allow_files':files,
           'worker_config':str(worker) if worker else None,'router_config':str(router_plan) if router_plan else None,
           'router_generated':bool(router_generated),
           'orchestrator_config':str(orchestrator_plan) if orchestrator_plan else None,
           'orchestrator_generated':bool(orchestrator_generated),
           'orchestration_preference':orchestration,
           'model_policy_preset':model_preset,
           'credential_env_names':credentials,
           'compaction_mode':compaction,
           'compaction_preference':compaction,
           'compaction_hosts':agents if compaction=='on' else [],
           'compaction_policy':str(compaction_policy) if compaction=='on' else None,
           'compaction_api_key_env':args.typesafe_env if compaction=='on' else None,
           'guard_mode':guard,'activation_mode':activation,'activation_limits':existing.get('activation_limits',{}) if existing else {},
           'configured_at':existing.get('configured_at',int(time.time())) if existing else int(time.time()),
           'updated_at':int(time.time()),'skill_hashes':{},'registrations':{}}
    planned_states=all_states(extra=state)
    global_actions=sync_global_mcp(planned_states,dry_run=True,replace=args.replace_existing_mcp)
    guard_rows=run_guard_setup(root,agents,guard,dry_run=True,
                               allow_tracked=args.allow_tracked_config)
    compaction_rows=sync_compaction_hook_setup(
        root,agents,old_compaction_hosts,compaction,dry_run=True,
        allow_tracked=args.allow_tracked_config)
    if args.dry_run:
        actions={'marker':marker_action,'runtime':runtime_action,
                 **{f'skill:{k}':v for k,v in skill_actions.items()},
                 'codex_mcp':global_actions['codex'][1],
                 'cursor_mcp':global_actions['cursor'][1],
                 'claude_mcp':global_actions['claude-code'][1],
                 'orchestrator_config':'write' if orchestrator_generated else 'off',
                 'compaction_policy':compaction_policy_action,
                 'compaction_plugin':compaction_plugin_action,
                 'claude_function_hooks':function_hooks_action}
        print_setup_plan(root,state,actions,guard_rows,compaction_rows,args.json); return 0

    # Phase 2: apply only after every collision/policy check above has passed.
    ensure_marker(root,dry_run=False,project_id=project_id)
    install_runtime(dry_run=False)
    if claude_compaction:
        # A global plugin without a project policy is inert. Install it before
        # enabling the function-hook flag; the project policy itself is written
        # only inside the transactional state/registration block below.
        ensure_claude_compaction_plugin(dry_run=False)
        ensure_claude_function_hooks_flag(dry_run=False)
    router,router_generated=make_router_config(root,project_id,router_mode,args.typesafe_env,
                                               router_source,dry_run=False)
    previous_orchestrator_path=(Path(existing['orchestrator_config'])
                                if existing and existing.get('orchestrator_generated')
                                and existing.get('orchestrator_config') else None)
    previous_orchestrator_bytes=(previous_orchestrator_path.read_bytes()
                                 if previous_orchestrator_path and previous_orchestrator_path.is_file()
                                 else None)
    orchestrator,orchestrator_generated=make_orchestrator_config(
        project_id,orchestration,args.typesafe_env,worker,dry_run=False)
    state['router_config']=str(router) if router else None
    state['router_generated']=bool(router_generated)
    state['orchestrator_config']=str(orchestrator) if orchestrator else None
    state['orchestrator_generated']=bool(orchestrator_generated)
    state['credential_env_names']=credential_names(
        worker,router,args.typesafe_env if router_generated else None,
        args.typesafe_env if compaction=='on' else None,
        orchestrator,args.typesafe_env if orchestrator_generated else None)
    for agent in agents:
        install_skill(root,agent,args.update,dry_run=False)
    audit.mkdir(parents=True,exist_ok=True)
    for agent in agents:
        state['skill_hashes'][agent]=tree_hash(root/AGENT_DIR[agent])
    previous_path=Path(existing['_state_path']) if existing else None
    previous_bytes=previous_path.read_bytes() if previous_path and previous_path.is_file() else None
    path=save_state(state)
    try:
        registrations=sync_global_mcp(all_states(extra=state),replace=args.replace_existing_mcp)
        state['registrations']={k:{'target':str(v[0]) if v[0] else None,'status':v[1]}
                                for k,v in registrations.items()}
        run_guard_setup(root,agents,guard,allow_tracked=args.allow_tracked_config)
        sync_compaction_hook_setup(
            root,agents,old_compaction_hosts,compaction,
            allow_tracked=args.allow_tracked_config)
        sync_compaction_policy(root,compaction,args.typesafe_env,agents,dry_run=False)
        save_state(state)
        if (existing and existing.get('orchestrator_generated')
                and existing.get('orchestrator_config') != state.get('orchestrator_config')):
            remove_generated_config(existing.get('orchestrator_config'))
    except Exception:
        # Restore state/global registration. The global compaction plugin may
        # remain installed, but without a project policy it is inert.
        try:
            if existing and existing.get('compaction_mode') == 'on':
                old_env=existing.get('compaction_api_key_env') or 'TYPESAFE_API_KEY'
                old_hosts=configured_compaction_hosts(existing)
                sync_compaction_hook_setup(root,old_hosts,agents,'on',allow_tracked=True)
                sync_compaction_policy(root,'on',old_env,old_hosts,dry_run=False)
            else:
                try: sync_compaction_hook_setup(root,agents,old_compaction_hosts,'off',allow_tracked=True)
                except Exception: pass
                sync_compaction_policy(root,'off',args.typesafe_env,agents,dry_run=False)
            if previous_bytes is not None and previous_path:
                atomic_write(previous_path,previous_bytes)
            elif path.is_file(): path.unlink()
            sync_global_mcp(all_states(exclude_id=project_id),replace=False)
            if orchestrator_generated and orchestrator:
                current_orchestrator=Path(orchestrator)
                if (previous_orchestrator_path
                        and current_orchestrator == previous_orchestrator_path
                        and previous_orchestrator_bytes is not None):
                    atomic_write(previous_orchestrator_path,previous_orchestrator_bytes)
                elif (not previous_orchestrator_path
                      or current_orchestrator != previous_orchestrator_path):
                    remove_generated_config(current_orchestrator)
        except Exception: pass
        raise
    print(f'I/O Delegation ready for {root}')
    print('Agents: '+', '.join(agents))
    print(f'Context scope: {len(prefixes)} folders + {len(files)} root files')
    print('Smart routing: '+('TypeSafe Jev' if router else 'local rules'))
    print('Compute orchestration: '+(('Jev host-aware ('+model_preset+')') if orchestrator else 'off'))
    print('Jev compaction: '+('automatic for '+', '.join(agents) if compaction=='on' else 'off'))
    print('Semantic worker: '+('configured' if worker else 'off'))
    print('Read guard: '+guard)
    print(f'State: {path}')
    if not args.no_doctor:
        return command_doctor(argparse.Namespace(project=str(root),json=False,quiet=True))
    return 0


def remove_skill_copies(root,state,force=False,dry_run=False):
    rows=[]; seen=set()
    hashes=state.get('skill_hashes',{})
    for agent in state.get('agents',[]):
        dest=Path(root)/AGENT_DIR[agent]
        if dest in seen or not dest.exists(): continue
        seen.add(dest); current=tree_hash(dest)
        expected=hashes.get(agent)
        safe=bool(expected and current==expected)
        if not safe and not force:
            rows.append({'path':str(dest),'status':'preserved_modified'}); continue
        if dry_run:
            rows.append({'path':str(dest),'status':'would_remove'}); continue
        shutil.rmtree(dest); rows.append({'path':str(dest),'status':'removed'})
    return rows


def cleanup_marker(root,dry_run=False):
    folder=Path(root)/MARKER_DIR
    if not folder.exists(): return 'absent'
    allowed={'project.json','compaction.json','.gitignore'}
    names={p.name for p in folder.iterdir()}
    if names-allowed: return 'preserved_nonmanaged_files'
    if dry_run: return 'would_remove'
    shutil.rmtree(folder); return 'removed'


def command_remove(args):
    root=Path(args.project).expanduser().resolve(strict=True); state=load_state(root)
    pid=state['project_id']; remaining=all_states(exclude_id=pid)
    guard_plan=remove_guard(root,state.get('agents',[]),dry_run=True,
                            allow_tracked=args.allow_tracked_config)
    compaction_hosts=configured_compaction_hosts(state)
    compaction_plan=sync_compaction_hook_setup(
        root,[],compaction_hosts,'off',dry_run=True,allow_tracked=args.allow_tracked_config)
    skill_plan=remove_skill_copies(root,state,args.force,dry_run=True)
    global_plan=sync_global_mcp(remaining,dry_run=True,replace=False)
    marker_plan=cleanup_marker(root,dry_run=True)
    plan={'project':str(root),'project_id':pid,'skills':skill_plan,'guard':guard_plan,
          'compaction_hooks':compaction_plan,'marker':marker_plan,'global_mcp':{k:v[1] for k,v in global_plan.items()},
          'audit':'remove' if args.purge_data else 'preserve',
          'runtime':'remove' if args.purge_runtime and not remaining else 'preserve'}
    if args.dry_run:
        if args.json: print(json.dumps(plan,ensure_ascii=False,indent=2))
        else:
            print('I/O Delegation removal preview (no files changed)')
            for k,v in plan.items(): print(f'{k}: {v}')
        return 0
    sync_global_mcp(remaining,replace=False)
    guard_rows=remove_guard(root,state.get('agents',[]),allow_tracked=args.allow_tracked_config)
    sync_compaction_hook_setup(root,[],compaction_hosts,'off',allow_tracked=args.allow_tracked_config)
    skills=remove_skill_copies(root,state,args.force)
    marker_action=cleanup_marker(root)
    path=Path(state['_state_path'])
    if path.is_file(): path.unlink()
    if state.get('router_generated') and state.get('router_config'):
        candidate=Path(state['router_config'])
        try: candidate.resolve().relative_to(configs_dir().resolve())
        except ValueError: pass
        else:
            if candidate.is_file(): candidate.unlink()
    if state.get('orchestrator_generated') and state.get('orchestrator_config'):
        remove_generated_config(state.get('orchestrator_config'))
    if args.purge_data: shutil.rmtree(state.get('audit_root',''),ignore_errors=True)
    if args.purge_runtime and not remaining: shutil.rmtree(USER_ROOT/'runtime',ignore_errors=True)
    print(f'I/O Delegation removed from {root}')
    for row in skills:
        if row['status']=='preserved_modified': print('Preserved modified skill: '+row['path'])
    print('Backups were preserved. Use `io-delegation backups` to inspect them.')
    return 0

def command_backups(args):
    rows=list_backups()
    if args.json:
        print(json.dumps(rows,ensure_ascii=False,indent=2)); return 0
    if not rows:
        print('No config backups recorded.'); return 0
    print('I/O Delegation config backups')
    for row in rows:
        state='restored' if row.get('restored_at') else 'available'
        print(f"{row['id']}  {row.get('label','')}  {state}  -> {row.get('target','')}")
    return 0


def command_restore(args):
    if args.dry_run:
        rows={r['id']:r for r in list_backups()}
        row=rows.get(args.backup)
        if not row: raise ValueError('Unknown backup id')
        current=file_hash(Path(row['target']))
        safe=current==row.get('after_sha256')
        data={'backup':args.backup,'target':row['target'],'safe_without_force':safe,'writes':False}
        if args.json: print(json.dumps(data,ensure_ascii=False,indent=2))
        else: print(f"Restore preview: {row['target']} ({'safe' if safe else 'changed; would require --force'})")
        return 0
    target=restore_backup(args.backup,args.force)
    print('Restored: '+str(target)); return 0


def audit_summary(state):
    path=Path(state['audit_root'])/'context-events.jsonl'; rows=[]
    if path.is_file():
        for line in path.read_text(encoding='utf-8',errors='replace').splitlines()[-400:]:
            try:
                row=json.loads(line)
                if row.get('event')=='operation_completed': rows.append(row)
            except ValueError: pass
    routes={}
    for row in rows:
        route=row.get('route','unknown'); routes[route]=routes.get(route,0)+1
    tiers={}
    for row in rows:
        tier=row.get('compute_tier')
        if tier: tiers[tier]=tiers.get(tier,0)+1
    return {'operations':len(rows),'routes':routes,'compute_tiers':tiers,
            'model_calls':sum(int(r.get('model_calls',0) or 0) for r in rows),
            'router_calls':sum(int(r.get('router_calls',0) or 0) for r in rows),
            'orchestrator_calls':sum(int(r.get('orchestrator_calls',0) or 0) for r in rows),
            'escalations':sum(r.get('escalated') is True for r in rows),
            'errors':sum(r.get('status') not in ('ok','insufficient_context') for r in rows),
            'cache_hits':sum(r.get('cache')=='hit' for r in rows)}


def codex_registered():
    path=codex_config_path()
    if not path.is_file(): return False
    text=path.read_text(encoding='utf-8-sig',errors='replace')
    return CODEX_BEGIN in text and CODEX_END in text and 'context_bootstrap.py' in text


def cursor_registered():
    row=read_json(cursor_config_path(),{})
    if not isinstance(row,dict): return False
    servers=row.get('mcpServers',{})
    return isinstance(servers,dict) and owned_cursor_entry(servers.get(SERVER_NAME))


def claude_registered():
    owned,_=claude_server_owned(); return owned


def registration_present(agent):
    if agent=='codex': return codex_registered()
    if agent=='cursor': return cursor_registered()
    if agent=='claude-code': return claude_registered()
    return False


def status_data(root):
    root=Path(root).resolve(strict=True); state=load_state(root)
    guard_path=root/'.io-delegation-hooks/policy.json'
    guard=read_json(guard_path,{}) if guard_path.is_file() else {}
    envs=env_names_for_state(state)
    return {'project':str(root),'stored_project':state.get('project'),'project_id':state['project_id'],
            'moved':str(root)!=state.get('project'),'agents':{a:registration_present(a) for a in state.get('agents',[])},
            'scope':{'prefixes':state.get('allow_prefixes',[]),'files':state.get('allow_files',[])},
            'runtime':runtime_bootstrap().is_file(),'smart_routing':bool(state.get('router_config')),
            'compute_orchestration':bool(state.get('orchestrator_config')),
            'orchestration_preference':state.get('orchestration_preference','auto'),
            'jev_compaction':state.get('compaction_mode')=='on',
            'compaction_preference':state.get('compaction_preference',state.get('compaction_mode','on')),
            'compaction_hosts':configured_compaction_hosts(state),
            'compaction_adapters':{
                a:compaction_hook_installed(root,a)
                for a in configured_compaction_hosts(state)
            } if state.get('compaction_mode')=='on' else {},
            'credential_envs':{name:bool(os.environ.get(name)) for name in envs},
            'semantic_worker':bool(state.get('worker_config')),
            'worker_auto_dispatch':worker_auto_dispatch(state.get('worker_config')),
            'guard':guard.get('mode',state.get('guard_mode','off')),
            'activation_mode':state.get('activation_mode','auto'),
            'audit':audit_summary(state)}


def command_status(args):
    data=status_data(Path(args.project).expanduser())
    if args.json:
        print(json.dumps(data,ensure_ascii=False,indent=2)); return 0
    print('I/O Delegation')
    print('Project       '+data['project'])
    print('Project id    '+data['project_id'])
    if data['moved']: print('Path          moved since last setup; rerun setup to refresh metadata')
    for agent,ok in data['agents'].items(): print(f"{agent:<13} {'configured' if ok else 'missing/pending'}")
    print(f"Context scope {len(data['scope']['prefixes'])} folders + {len(data['scope']['files'])} files")
    print('Runtime       '+('ready' if data['runtime'] else 'missing'))
    print('Jev router    '+('enabled' if data['smart_routing'] else 'off'))
    print('Compute       '+('cheap-first' if data['compute_orchestration'] else 'off'))
    if data['jev_compaction']:
        detail=', '.join(f"{a}:{'ready' if ok else 'missing'}" for a,ok in data['compaction_adapters'].items())
        print('Compaction    automatic | '+detail)
    else:
        print('Compaction    off')
    for name,present in data['credential_envs'].items(): print(f"Credential    {name}: {'available' if present else 'missing'}")
    if data['semantic_worker']:
        print('Worker        configured; experimental auto-dispatch '+('on' if data['worker_auto_dispatch'] else 'off'))
    else:
        print('Worker        off')
    print('Read guard    '+str(data['guard']))
    print('Activation    '+str(data['activation_mode']))
    a=data['audit']; print(f"Activity      {a['operations']} ops | {a['router_calls']} routed | {a['orchestrator_calls']} compute | {a['model_calls']} worker calls | {a['escalations']} escalations | {a['errors']} errors")
    if a['routes']: print('Routes        '+', '.join(f'{k}:{v}' for k,v in sorted(a['routes'].items())))
    return 0


def mcp_probe(root,state):
    root=Path(root).resolve(strict=True)
    if not runtime_bootstrap().is_file(): raise ValueError('Global runtime is missing')
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'io-delegation-doctor','version':'1'}}},
              {'jsonrpc':'2.0','method':'notifications/initialized'},
              {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}]
    raw=''.join(json.dumps(x,separators=(',',':'))+'\n' for x in messages).encode('utf-8')
    env=os.environ.copy(); env['IO_DELEGATION_FORCE']='on'
    cp=subprocess.run([sys.executable,str(runtime_bootstrap())],cwd=root,input=raw,
                      capture_output=True,timeout=20,env=env)
    if cp.returncode: raise ValueError('Context MCP failed startup preflight')
    replies=[]
    for line in cp.stdout.splitlines():
        try: replies.append(json.loads(line))
        except ValueError: pass
    listed=next((x for x in replies if x.get('id')==2),None)
    if not listed or 'result' not in listed: raise ValueError('Context MCP did not return tools/list')
    return [x['name'] for x in listed['result']['tools']]


def command_doctor(args):
    root=Path(args.project).expanduser().resolve(strict=True); state=load_state(root)
    checks=[]
    def add(name,status,detail=''): checks.append({'name':name,'status':status,'detail':detail})
    marker=marker_id(root); add('project marker','pass' if marker==state['project_id'] else 'fail',str(marker or 'missing'))
    add('runtime','pass' if runtime_bootstrap().is_file() else 'fail',str(runtime_bootstrap()))
    if state.get('project') != str(root): add('project path','warn','moved; rerun setup to refresh stored metadata')
    else: add('project path','pass',str(root))
    for agent in state.get('agents',[]):
        reg=registration_present(agent)
        status='pass' if reg else ('warn' if agent=='claude-code' and not claude_cli() else 'fail')
        detail=('Claude CLI is not installed; MCP registration is pending' if status=='warn' else '')
        add(f'{agent} MCP',status,detail)
        skill=root/AGENT_DIR[agent]/'SKILL.md'; add(f'{agent} skill','pass' if skill.is_file() else 'fail',str(skill))
    add('audit directory','pass' if Path(state['audit_root']).is_dir() else 'fail',state['audit_root'])
    try:
        tools=mcp_probe(root,state); needed={'search','extract','query'}
        add('MCP handshake','pass' if set(tools)==needed and len(tools)==len(needed) else 'fail',', '.join(tools))
    except Exception as exc: add('MCP handshake','fail',str(exc))
    if state.get('router_config'):
        try:
            sys.path.insert(0,str(SKILL_SOURCE/'scripts')); import decision_router
            cfg=decision_router.load_config(state['router_config']); key=cfg['api_key_env']
            add('Jev config','pass',state['router_config']); add('Jev key','pass' if os.environ.get(key) else 'warn',key)
        except Exception as exc: add('Jev config','fail',str(exc))
    if state.get('orchestrator_config'):
        try:
            sys.path.insert(0,str(SKILL_SOURCE/'scripts')); import context_orchestrator
            cfg=context_orchestrator.load_config(state['orchestrator_config']); key=cfg['api_key_env']
            add('Jev compute orchestration','pass',state['orchestrator_config'])
            add('Jev compute key','pass' if os.environ.get(key) else 'warn',key)
            add('cheap-first worker','pass' if state.get('worker_config') else 'fail',
                state.get('worker_config') or 'missing')
        except Exception as exc:
            add('Jev compute orchestration','fail',str(exc))
    if state.get('compaction_mode') == 'on':
        policy=read_json(compaction_policy_path(root),{})
        valid=(isinstance(policy,dict) and policy.get('version')==1 and policy.get('enabled') is True and
               policy.get('provider')=='typesafe' and policy.get('approved_data_scope')==COMPACTION_DATA_SCOPE)
        add('Jev compaction policy','pass' if valid else 'fail',str(compaction_policy_path(root)))
        hosts=configured_compaction_hosts(state)
        for agent in hosts:
            ready=compaction_hook_installed(root,agent)
            label='Jev compaction '+agent
            detail=(COMPACTION_PLUGIN_NAME if agent=='claude-code' else str(compaction_hook_record(root,agent)))
            add(label,'pass' if ready else 'warn',detail if ready else 'adapter missing/pending')
        key=state.get('compaction_api_key_env') or 'TYPESAFE_API_KEY'
        add('Jev compaction key','pass' if os.environ.get(key) else 'warn',key)
    if state.get('worker_config'):
        try:
            validate_worker(root,state['worker_config']); add('worker config','pass',state['worker_config'])
            enabled=worker_auto_dispatch(state['worker_config'])
            add('worker auto-dispatch','warn' if enabled else 'pass',
                'experimental opt-in enabled' if enabled else 'off')
        except Exception as exc: add('worker config','fail',str(exc))
    guard=root/'.io-delegation-hooks/policy.json'
    if state.get('guard_mode')!='off':
        row=read_json(guard,{}) if guard.is_file() else {}
        add('read guard','pass' if row.get('mode') in ('observe','enforce') else 'fail',str(row.get('mode','missing')))
    if args.json: print(json.dumps(checks,ensure_ascii=False,indent=2))
    else:
        print('Doctor')
        marks={'pass':'[ok]','warn':'[warn]','fail':'[fail]'}
        for row in checks: print(f"{marks[row['status']]} {row['name']}: {row['detail']}".rstrip(': '))
    return 2 if any(x['status']=='fail' for x in checks) else 0



def command_gate(args):
    root=Path(args.project).expanduser().resolve(strict=True); state=load_state(root)
    sys.path.insert(0,str(SKILL_SOURCE/'scripts')); from context_activation import decide
    row=decide(root,state,args.task,args.force)
    if args.json: print(json.dumps(row,ensure_ascii=False,indent=2))
    else:
        print(f"{row['decision']}  {row['reason']}")
        print(json.dumps(row.get('signals',{}),ensure_ascii=False,sort_keys=True))
    return 0


def build_parser():
    p=argparse.ArgumentParser(prog='io-delegation',description='Context gateway for coding agents')
    sub=p.add_subparsers(dest='command',required=True)
    setup=sub.add_parser('setup',help='Install or refresh the context gateway')
    setup.add_argument('--project',default='.')
    setup.add_argument('--agent',action='append',choices=['auto','all','codex','claude-code','cursor'])
    setup.add_argument('--allow-prefix',action='append'); setup.add_argument('--allow-file',action='append')
    setup.add_argument('--worker-config'); setup.add_argument('--no-worker',action='store_true')
    setup.add_argument('--router-config'); setup.add_argument('--jev',choices=['auto','on','off'],default=None)
    setup.add_argument('--orchestration',choices=['auto','on','off'],default=None,
                       help='Jev model orchestration; auto enables it when a worker is configured')
    setup.add_argument('--model-preset',choices=['cost','balanced','quality'],default=None,
                       help='host-aware model selection preset; default balanced')
    setup.add_argument('--compaction',choices=['on','off'],default=None,
                       help='automatic by default; off is a persistent per-project override')
    setup.add_argument('--typesafe-env',default='TYPESAFE_API_KEY')
    setup.add_argument('--guard',choices=['off','observe','enforce'],default=None)
    setup.add_argument('--activation',choices=['auto','always','off'],default=None)
    setup.add_argument('--update',action='store_true'); setup.add_argument('--no-doctor',action='store_true')
    setup.add_argument('--dry-run',action='store_true'); setup.add_argument('--json',action='store_true')
    setup.add_argument('--replace-existing-mcp',action='store_true')
    setup.add_argument('--allow-tracked-config',action='store_true')
    setup.set_defaults(func=command_setup)

    remove=sub.add_parser('remove',help='Remove this project without touching unrelated settings')
    remove.add_argument('--project',default='.'); remove.add_argument('--dry-run',action='store_true')
    remove.add_argument('--json',action='store_true'); remove.add_argument('--force',action='store_true')
    remove.add_argument('--purge-data',action='store_true'); remove.add_argument('--purge-runtime',action='store_true')
    remove.add_argument('--allow-tracked-config',action='store_true'); remove.set_defaults(func=command_remove)

    gate=sub.add_parser('gate',help='Preview the deterministic activation decision')
    gate.add_argument('--project',default='.'); gate.add_argument('--task'); gate.add_argument('--force',choices=['on','off'])
    gate.add_argument('--json',action='store_true'); gate.set_defaults(func=command_gate)

    for name,func in [('status',command_status),('doctor',command_doctor)]:
        cmd=sub.add_parser(name); cmd.add_argument('--project',default='.')
        cmd.add_argument('--json',action='store_true'); cmd.set_defaults(func=func)
    backups=sub.add_parser('backups'); backups.add_argument('--json',action='store_true'); backups.set_defaults(func=command_backups)
    restore=sub.add_parser('restore'); restore.add_argument('backup'); restore.add_argument('--dry-run',action='store_true')
    restore.add_argument('--json',action='store_true'); restore.add_argument('--force',action='store_true'); restore.set_defaults(func=command_restore)
    return p


def main(argv=None):
    try:
        args=build_parser().parse_args(argv); return args.func(args)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError,json.JSONDecodeError) as exc:
        print(f'io-delegation: {exc}',file=sys.stderr); return 2


if __name__=='__main__':
    raise SystemExit(main())
