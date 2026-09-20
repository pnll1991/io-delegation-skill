from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/io-delegation/scripts'))
import model_policy as policy


def scores(cheap=.9, reasoning=.1, risk=.05, uncertainty=.1, parallel=.1):
    return {
        'cheap_model_sufficient': cheap,
        'reasoning_required': reasoning,
        'risk_high': risk,
        'uncertainty_high': uncertainty,
        'parallelism_useful': parallel,
    }


class ModelPolicyTests(unittest.TestCase):
    def test_default_codex_ceiling_is_astra_low(self):
        row=policy.resolve({'adapter':'host-cli'},'codex')
        self.assertEqual(row['max_profile'],'astra-low')
        self.assertEqual(row['order'][-1],'astra-low')
        astra=row['table']['astra-low']
        self.assertEqual(astra['model'],'gpt-6-astra')
        self.assertEqual(astra['effort'],'low')
        self.assertFalse(any(x['model']=='gpt-6-astra' and x['effort']!='low'
                             for x in row['profiles']))

    def test_default_cursor_does_not_include_astra(self):
        row=policy.resolve({'adapter':'host-cli'},'cursor')
        self.assertEqual(row['max_profile'],'sol-high')
        self.assertNotIn('astra-low',row['table'])
        self.assertEqual(row['order'],['luna-medium','luna-high','sol-medium','sol-high'])

    def test_cost_preset_low_demand_starts_luna_medium(self):
        row=policy.choose({'adapter':'host-cli'},'codex',scores(),operation='factual',
                          preset_override='cost')
        self.assertEqual(row['decision'],'worker')
        self.assertEqual(row['profile']['id'],'luna-medium')
        self.assertEqual(row['model_tier'],'M1')

    def test_balanced_low_demand_prefers_luna_high_quality_margin(self):
        row=policy.choose({'adapter':'host-cli'},'codex',scores(),operation='factual')
        self.assertEqual(row['profile']['id'],'luna-high')
        self.assertEqual(row['model_tier'],'M2')

    def test_medium_demand_can_jump_directly_to_terra(self):
        row=policy.choose({'adapter':'host-cli'},'codex',
                          scores(cheap=.45,reasoning=.54,risk=.2,uncertainty=.25),
                          operation='factual')
        self.assertEqual(row['profile']['id'],'terra-medium')

    def test_hard_codex_task_can_start_at_astra_low(self):
        row=policy.choose({'adapter':'host-cli'},'codex',
                          scores(cheap=.02,reasoning=.96,risk=.7,uncertainty=.8),
                          operation='factual')
        self.assertEqual(row['profile']['id'],'astra-low')
        self.assertEqual(row['profile']['effort'],'low')

    def test_live_like_metadata_uncertainty_does_not_buy_astra(self):
        cfg={'adapter':'host-cli'}
        easy=policy.choose(cfg,'codex',
            scores(cheap=.68,reasoning=.08,risk=.18,uncertainty=.91,parallel=.26),
            operation='factual')
        medium=policy.choose(cfg,'codex',
            scores(cheap=.76,reasoning=.08,risk=.21,uncertainty=.86,parallel=.45),
            operation='factual')
        hard=policy.choose(cfg,'codex',
            scores(cheap=.67,reasoning=.11,risk=.27,uncertainty=.83,parallel=.48),
            operation='factual')
        self.assertEqual(easy['profile']['id'],'luna-high')
        self.assertEqual(medium['profile']['id'],'luna-high')
        self.assertEqual(hard['profile']['id'],'luna-high')

    def test_codex_cost_weights_track_published_price_order(self):
        table=policy.resolve({'adapter':'host-cli'},'codex')['table']
        costs=[table[x]['cost_index'] for x in
               ('luna-medium','luna-high','terra-medium','sol-medium','astra-low')]
        self.assertEqual(costs,sorted(costs))
        self.assertLess(table['sol-medium']['cost_index'],table['astra-low']['cost_index']/2)
        self.assertLess(table['luna-high']['cost_index'],table['terra-medium']['cost_index']/5)

    def test_sensitive_operations_never_delegate_even_when_cheap(self):
        for op in ('debugging','architecture','security','editing','generation'):
            row=policy.choose({'adapter':'host-cli'},'codex',scores(),operation=op)
            self.assertEqual(row['decision'],'principal')
            self.assertEqual(row['reason'],'sensitive_operation')

    def test_quality_preset_escalates_earlier_than_cost(self):
        s=scores(cheap=.55,reasoning=.42,risk=.12,uncertainty=.18)
        cheap=policy.choose({'adapter':'host-cli'},'codex',s,'factual','cost')
        quality=policy.choose({'adapter':'host-cli'},'codex',s,'factual','quality')
        order=['luna-medium','luna-high','terra-medium','sol-medium','astra-low']
        self.assertLessEqual(order.index(cheap['profile']['id']),
                             order.index(quality['profile']['id']))

    def test_user_can_lower_ceiling_and_block_models(self):
        cfg={'adapter':'host-cli','model_policy':{'hosts':{'codex':{
            'max_profile':'sol-medium',
            'blocked_profiles':['terra-medium'],
        }}}}
        row=policy.resolve(cfg,'codex')
        self.assertEqual(row['order'],['luna-medium','luna-high','sol-medium'])
        hard=policy.choose(cfg,'codex',
                           scores(cheap=.01,reasoning=.99,risk=.8,uncertainty=.9),
                           'factual')
        self.assertEqual(hard['decision'],'principal')
        self.assertEqual(hard['reason'],'model_ceiling_insufficient')

    def test_user_can_replace_registry_and_ceiling(self):
        cfg={'adapter':'host-cli','model_policy':{'hosts':{'codex':{
            'profiles':[
                {'id':'custom-small','model':'my-small','effort':'low','capability':.5,'cost_index':.1},
                {'id':'custom-max','model':'my-max','effort':'medium','capability':1.0,'cost_index':1.0},
            ],
            'orders':{
                'cost':['custom-small','custom-max'],
                'balanced':['custom-small','custom-max'],
                'quality':['custom-max'],
            },
            'max_profile':'custom-max',
        }}}}
        row=policy.resolve(cfg,'codex')
        self.assertEqual(row['order'],['custom-small','custom-max'])
        hard=policy.choose(cfg,'codex',scores(cheap=.01,reasoning=.99),'factual')
        self.assertEqual(hard['profile']['model'],'my-max')
        self.assertEqual(hard['profile']['effort'],'medium')

    def test_cursor_native_router_is_explicit_not_guessed(self):
        cfg={'adapter':'host-cli','model_policy':{'hosts':{'cursor':{
            'strategy':'native-router-first',
            'native_router_model':'auto',
        }}}}
        row=policy.choose(cfg,'cursor',scores(),operation='factual')
        self.assertEqual(row['profile']['id'],'cursor-native-router')
        self.assertEqual(row['profile']['cursor_model'],'auto')
        self.assertEqual(row['model_tier'],'M-auto')

    def test_apply_profile_resolves_host_cli(self):
        cfg={'adapter':'host-cli'}
        codex=policy.resolve(cfg,'codex')['table']['sol-medium']
        derived,meta=policy.apply_profile(cfg,'codex',codex)
        self.assertEqual(derived['adapter'],'codex-cli')
        self.assertEqual(derived['model'],'gpt-5.6-sol')
        self.assertEqual(derived['reasoning_effort'],'medium')
        cursor=policy.resolve(cfg,'cursor')['table']['luna-high']
        derived,meta=policy.apply_profile(cfg,'cursor',cursor)
        self.assertEqual(derived['adapter'],'cursor-cli')
        self.assertEqual(derived['cursor_model'],'gpt-5.6-luna[effort=high]')

    def test_cursor_cost_preset_can_end_below_global_ceiling(self):
        row=policy.resolve({'adapter':'host-cli'},'cursor','cost')
        self.assertEqual(row['max_profile'],'sol-high')
        self.assertEqual(row['order'][-1],'sol-medium')

    def test_custom_registry_infers_custom_ceiling(self):
        cfg={'adapter':'host-cli','model_policy':{'hosts':{'codex':{
            'profiles':[
                {'id':'a','model':'a-model','effort':'low','capability':.4,'cost_index':.1},
                {'id':'b','model':'b-model','effort':'medium','capability':1.0,'cost_index':.8},
            ],
            'orders':{'cost':['a','b'],'balanced':['a','b'],'quality':['b']}
        }}}}
        row=policy.resolve(cfg,'codex')
        self.assertEqual(row['max_profile'],'b')

    def test_host_specific_executable_is_applied(self):
        cfg={'adapter':'host-cli','codex_executable':'/approved/codex',
             'cursor_executable':'/approved/agent'}
        codex=policy.resolve(cfg,'codex')['table']['luna-medium']
        derived,_=policy.apply_profile(cfg,'codex',codex)
        self.assertEqual(derived['executable'],'/approved/codex')
        cursor=policy.resolve(cfg,'cursor')['table']['luna-medium']
        derived,_=policy.apply_profile(cfg,'cursor',cursor)
        self.assertEqual(derived['executable'],'/approved/agent')

    def test_next_profile_respects_effective_order(self):
        cfg={'adapter':'host-cli'}
        nxt=policy.next_profile(cfg,'cursor','luna-high')
        self.assertEqual(nxt['profile']['id'],'sol-medium')
        self.assertEqual(nxt['model_tier'],'M3')


if __name__=='__main__':
    unittest.main()
