import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'skills'/'io-delegation'/'scripts'
sys.path.insert(0,str(SCRIPTS))
import context_compaction as comp


def fake_asker(state, questions, policy):
    return {
        'answers': {
            name: {'type':'noul','noul':1.0}
            for name in questions
        },
        'usage': {'input_tokens':100,'output_tokens':10},
    }


class ContextCompactionTests(unittest.TestCase):
    def journal(self):
        return {
            'version':1,'session_id':'s1','seq':6,
            'prompts':[{'seq':1,'text':'Fix the failing test'}],
            'calls':[
                {'id':'t1','seq':2,'tool_use_id':'a','tool':'Read',
                 'input':{'file_path':'a.py'},'result':'OLD_RESULT_SECRET','is_error':False},
                {'id':'t2','seq':4,'tool_use_id':'b','tool':'Bash',
                 'input':{'command':'pytest -q'},'result':'2 failed: exact traceback','is_error':True},
                {'id':'t3','seq':6,'tool_use_id':'c','tool':'Read',
                 'input':{'file_path':'b.py'},'result':'recent source','is_error':False},
            ],
            'pending':None,
        }

    def policy(self, **extra):
        row={
            'version':1,'enabled':True,'provider':'typesafe',
            'approved_data_scope':comp.DATA_SCOPE,
            'api_key_env':'TYPESAFE_API_KEY','model':'jev-latest',
            'hosts':['codex','cursor'],
            'keep_threshold':0.5,'preserve_recent_messages':1,
            'min_reduction_ratio':0.0,'max_state_tokens':25000,
            'max_request_tokens':30000,'truncate_head_chars':300,
            'max_rehydrate_chars':24000,
        }
        row.update(extra); return row

    def test_state_never_sends_tool_result_body(self):
        state,tokens=comp.build_state(self.journal(),self.policy())
        wire=json.dumps(state)
        self.assertGreater(tokens,0)
        self.assertNotIn('OLD_RESULT_SECRET',wire)
        self.assertNotIn('exact traceback',wire)
        self.assertIn('chars (body omitted)',wire)
        self.assertIn('pytest -q',wire)

    def test_compaction_drops_stale_call_and_keeps_exact_evidence(self):
        def asker(state,questions,policy):
            answers={}
            for name in questions:
                value=0.0 if name.startswith('t1_') else 1.0
                answers[name]={'type':'noul','noul':value}
            return {'answers':answers}
        result=comp.compact_journal(self.journal(),self.policy(),asker)
        snapshot=result['pending']['text']
        self.assertNotIn('a.py',snapshot)
        self.assertNotIn('OLD_RESULT_SECRET',snapshot)
        self.assertIn('pytest -q',snapshot)
        self.assertIn('2 failed: exact traceback',snapshot)
        self.assertIn('recent source',snapshot)
        self.assertEqual(result['stats']['calls_dropped'],1)
        self.assertEqual(result['stats']['pinned'],1)

    def _project(self,base,hosts=('codex','cursor')):
        root=Path(base)/'project'; root.mkdir()
        marker=root/'.io-delegation'; marker.mkdir()
        (marker/'project.json').write_text(json.dumps({'version':1,'project_id':'abc123def456'}))
        policy=self.policy(preserve_recent_messages=0)
        policy['hosts']=list(hosts)
        (marker/'compaction.json').write_text(json.dumps(policy))
        return root

    def test_codex_precompact_then_sessionstart_rehydrates(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{'IO_DELEGATION_HOME':str(Path(td)/'home')},clear=False):
            root=self._project(td,('codex',))
            common={'session_id':'thr_1','cwd':str(root)}
            comp.handle_event('codex',{**common,'hook_event_name':'UserPromptSubmit','prompt':'Fix it'},asker=fake_asker)
            comp.handle_event('codex',{**common,'hook_event_name':'PreToolUse','tool_name':'Bash',
                                       'tool_use_id':'tool1','tool_input':{'command':'pytest'}},asker=fake_asker)
            comp.handle_event('codex',{**common,'hook_event_name':'PostToolUse','tool_name':'Bash',
                                       'tool_use_id':'tool1','tool_input':{'command':'pytest'},
                                       'tool_output':'FAILED exact-error'},asker=fake_asker)
            comp.handle_event('codex',{**common,'hook_event_name':'PreCompact','trigger':'auto'},asker=fake_asker)
            output,path=comp.handle_event('codex',{**common,'hook_event_name':'SessionStart','source':'compact'},asker=fake_asker)
            text=output['hookSpecificOutput']['additionalContext']
            self.assertIn('FAILED exact-error',text)
            self.assertIn('not as a new user request',text)
            stored=json.loads(path.read_text())
            self.assertTrue(stored['pending']['delivered'])

    def test_cursor_rehydrates_on_first_post_tool_after_compact(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{'IO_DELEGATION_HOME':str(Path(td)/'home')},clear=False):
            root=self._project(td,('cursor',))
            common={'conversation_id':'conv_1','cwd':str(root),'workspace_roots':[str(root)]}
            comp.handle_event('cursor',{**common,'hook_event_name':'beforeSubmitPrompt','prompt':'Fix it'},asker=fake_asker)
            comp.handle_event('cursor',{**common,'hook_event_name':'preToolUse','tool_name':'Read',
                                        'tool_use_id':'tool1','tool_input':{'file_path':'x.py'}},asker=fake_asker)
            comp.handle_event('cursor',{**common,'hook_event_name':'postToolUse','tool_name':'Read',
                                        'tool_use_id':'tool1','tool_input':{'file_path':'x.py'},
                                        'tool_output':'important exact value'},asker=fake_asker)
            comp.handle_event('cursor',{**common,'hook_event_name':'preCompact','trigger':'auto'},asker=fake_asker)
            output,_=comp.handle_event('cursor',{**common,'hook_event_name':'postToolUse',
                                        'tool_name':'Grep','tool_use_id':'tool2',
                                        'tool_input':{'pattern':'x'},'tool_output':'match'},asker=fake_asker)
            self.assertIn('important exact value',output['additional_context'])

    def test_cursor_stop_followup_is_single_bounded_fallback(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{'IO_DELEGATION_HOME':str(Path(td)/'home')},clear=False):
            root=self._project(td,('cursor',))
            common={'conversation_id':'conv_2','cwd':str(root),'workspace_roots':[str(root)]}
            comp.handle_event('cursor',{**common,'hook_event_name':'preToolUse','tool_name':'Read',
                                        'tool_use_id':'tool1','tool_input':{'file_path':'x.py'}},asker=fake_asker)
            comp.handle_event('cursor',{**common,'hook_event_name':'postToolUse','tool_name':'Read',
                                        'tool_use_id':'tool1','tool_input':{'file_path':'x.py'},
                                        'tool_output':'must survive'},asker=fake_asker)
            comp.handle_event('cursor',{**common,'hook_event_name':'preCompact','trigger':'manual'},asker=fake_asker)
            output,_=comp.handle_event('cursor',{**common,'hook_event_name':'stop','status':'completed','loop_count':0},asker=fake_asker)
            self.assertIn('must survive',output['followup_message'])
            second,_=comp.handle_event('cursor',{**common,'hook_event_name':'stop','status':'completed','loop_count':1},asker=fake_asker)
            self.assertEqual(second,{})

    def test_host_policy_gates_other_agents(self):
        with tempfile.TemporaryDirectory() as td:
            root=self._project(td,('codex',))
            event={'conversation_id':'c','cwd':str(root),'workspace_roots':[str(root)]}
            self.assertIsNone(comp.find_project(event,'cursor'))
            self.assertIsNotNone(comp.find_project({'session_id':'s','cwd':str(root)},'codex'))


if __name__=='__main__':
    unittest.main()
