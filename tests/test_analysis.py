"""检查解析、覆盖、比较、汇总与更正的行为。

这些测试锁定的是资料里的关键语义，不依赖具体措辞的偶然性：
* 没走完只算实际覆盖；"没按段数"的合计不能摊回各段；
* 同一袋只算一袋；合称不能拆到单岸；
* 空白不是零；天气/时长不达口径时观察保留、不参与比较；
* 未定归属改变结论时列为未决，而不是替分析员选；
* 汇总栏不能把共享批次的上限/下限相加；
* 更正能回到原栏目和原话。
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from beachwatch.analysis import (  # noqa: E402
    build_observations, build_states, compare_all, combined_weights,
    combine_intervals, display,
)
from beachwatch.model import load_records  # noqa: E402
from beachwatch.parser import parse_all  # noqa: E402
from beachwatch.report import build_report, review_bulletin, render_text  # noqa: E402


class Pipeline:
    records = load_records()
    parses = parse_all(records)
    observations, bags, unresolved = build_observations(records, parses)
    states = build_states(records, observations, unresolved, parses)
    comparisons = compare_all(records, states)


def parse(note_id):
    return Pipeline.parses[note_id]


class ParserTests(unittest.TestCase):
    def test_n2_range_is_cut_at_170m_and_nominal_end_kept(self):
        claim = parse('N2').coverage[0]
        self.assertEqual((claim.lo, claim.hi, claim.nominal_hi), (0, 170, 220))
        self.assertFalse(claim.complete)
        self.assertEqual(claim.start_name, '渡口')

    def test_n4_half_walked_and_not_weighed(self):
        claims = parse('N4').coverage
        self.assertEqual([(c.lo, c.hi, c.complete) for c in claims],
                         [(0, 120, True), (120, 190, False)])
        self.assertEqual(claims[1].nominal_hi, 260)
        kinds = {n.kind for n in parse('N4').negations}
        self.assertTrue({'absent', 'not_walked', 'not_weighed'} <= kinds)

    def test_n6_stops_at_half_of_bridge_segment_and_never_goes_down_sluice(self):
        claim = parse('N6').coverage[0]
        self.assertEqual((claim.lo, claim.hi, claim.nominal_hi), (220, 275, 330))
        self.assertFalse(claim.complete)
        self.assertIn('堤顶', ' '.join(parse('N6').collection_points))

    def test_n8_unclear_sighting_is_recorded_not_zero(self):
        quantities = [q for q in parse('N8').quantities if q.unit_kind == 'items']
        self.assertEqual(len(quantities), 1)
        self.assertTrue(quantities[0].unresolved)
        self.assertIsNone(quantities[0].value)

    def test_note_rates_are_read_from_text(self):
        self.assertEqual(parse('N1').coverage[0].rate_per_100m, 20.0)
        self.assertEqual(parse('N5').coverage[0].minutes, 24)


class ObservationTests(unittest.TestCase):
    def test_group_total_stays_a_group_and_gets_no_per_segment_split(self):
        group = [o for o in Pipeline.observations
                 if o.note_id == 'N3' and o.unit_kind == 'items'][0]
        self.assertEqual(group.status, 'group')
        self.assertEqual(set(group.seg_ids), {'L1', 'L2'})
        # 实际只走到 170 米：组总量的观察范围也被截到实际覆盖
        self.assertEqual((group.lo, group.hi, group.nominal_hi), (0, 170, 220))

    def test_n1_exact_segment_stays_exact_and_earlier_group_does_not_leak_in(self):
        obs = {(o.note_id, o.value): o for o in Pipeline.observations if o.unit_kind == 'items'}
        self.assertEqual(obs[('N1', 5.0)].seg_ids, ('L3',))
        self.assertEqual(obs[('N1', 5.0)].status, 'exact')

    def test_restated_weight_is_one_observation_with_mentions(self):
        six = [o for o in Pipeline.observations
               if o.unit_kind == 'weight' and o.value == 6.0]
        self.assertEqual(len(six), 1)
        self.assertEqual(set(six[0].mentions), {'N2', 'N7'})
        self.assertTrue(six[0].shared)

    def test_same_bag_is_one_unit_even_when_both_teams_mention_it(self):
        bag = [b for b in Pipeline.bags if b.batch == '柳17']
        self.assertEqual(len(bag), 1)
        self.assertEqual(set(bag[0].mentions), {'N2', 'N3', 'N8'})
        self.assertEqual(bag[0].value, 0.5)
        self.assertIsNotNone(bag[0].folded_into)

    def test_confirmed_absence_is_zero_but_unwalked_is_not(self):
        state = Pipeline.states[('暖季A', '右岸')]
        self.assertEqual(state.segments['R2'].item_status, 'zero')
        spring_b = Pipeline.states[('暖季B', '左岸')]
        self.assertEqual(spring_b.segments['L3'].item_status, 'no_data')

    def test_unresolved_sighting_belongs_to_no_single_segment(self):
        self.assertEqual(Pipeline.unresolved[0].bank, '右岸')
        state = Pipeline.states[('暖季B', '右岸')]
        self.assertEqual(state.segments['R2'].item_status, 'unresolved')


class ComparisonTests(unittest.TestCase):
    def by_key(self, round_id, bank):
        return next(c for c in Pipeline.comparisons
                    if c.round_id == round_id and c.bank == bank)

    def test_left_bank_items_are_bounded_not_declared_improved(self):
        c = self.by_key('R1', '左岸')
        self.assertEqual(c.status, 'bounded')
        self.assertEqual(c.direction, 'undetermined')
        self.assertEqual((c.a_upper, c.b_upper), (12.0, 10.0))
        self.assertIn('不能按平均', c.detail)
        # 只按合计所涉段里两期确实共有的范围计，不能拿全岸长度相除
        self.assertEqual(c.seg_ids, ('L1', 'L2'))
        self.assertEqual(c.overlap_m, 170)

    def test_right_bank_items_carry_pending_sighting_as_unresolved(self):
        c = self.by_key('R1', '右岸')
        self.assertEqual(c.status, 'unresolved')
        self.assertEqual(c.direction, 'undetermined')
        self.assertTrue(c.alternatives)
        self.assertIn('方向未定', c.detail)

    def test_weight_comparison_refuses_joint_and_off_caliber_weather(self):
        for bank in ('左岸', '右岸'):
            c = self.by_key('R2', bank)
            self.assertEqual(c.status, 'no_weight_pair')
            self.assertIn('两场合称', c.detail)

    def test_comparison_only_counts_the_compared_segment_overlap(self):
        c = self.by_key('R1', '右岸')
        self.assertEqual(c.seg_ids, ('R1',))
        self.assertEqual(c.overlap_m, 120)


class SummaryTests(unittest.TestCase):
    def test_combined_weights_refuse_to_add_across_batches(self):
        weights = [o for o in Pipeline.observations if o.unit_kind == 'weight']
        total = combined_weights(weights)
        self.assertEqual(total.status, 'not_additive')
        self.assertIsNone(total.value)
        self.assertIn('小计与总计', total.detail)

    def test_shared_batch_intervals_are_not_added_even_for_display_columns(self):
        rows = [r for r in display(Pipeline.records, Pipeline.states, 'per_bank')
                if r.season == '暖季B' and r.metric == 'weight']
        combined = combine_intervals(rows, Pipeline.observations)
        # 柳17 同时出现在左右两栏的引用里：不能把两栏上限或下限相加
        self.assertEqual(combined.status, 'not_additive')
        self.assertIsNone(combined.value)
        self.assertIn('柳17', combined.detail)

    def test_blank_column_blocks_a_bank_total_instead_of_counting_zero(self):
        rows = [r for r in display(Pipeline.records, Pipeline.states, 'per_bank')
                if r.season == '暖季B' and r.metric == 'items']
        combined = combine_intervals(rows, Pipeline.observations)
        self.assertEqual(combined.status, 'incomplete')
        self.assertIsNone(combined.value)

    def test_per_bank_display_has_ranges_and_no_fake_zero(self):
        rows = display(Pipeline.records, Pipeline.states, 'per_bank')
        l4 = next(r for r in rows if r.season == '暖季A' and r.bank == '左岸'
                  and r.metric == 'items')
        self.assertEqual(l4.scope, 'partial')
        self.assertEqual(l4.lower, 17.0)
        self.assertIsNone(l4.upper)
        b_weight = next(r for r in rows if r.season == '暖季B' and r.bank == '左岸'
                        and r.metric == 'weight')
        self.assertEqual(b_weight.scope, 'joint')
        self.assertIsNone(b_weight.value)

    def test_whole_bank_does_not_invent_a_point_value(self):
        rows = [r for r in display(Pipeline.records, Pipeline.states, 'whole_bank')
                if r.bank == '两岸']
        spring_a_items = next(r for r in rows
                              if r.season == '暖季A' and r.metric == 'items')
        self.assertEqual(spring_a_items.scope, 'mixed')
        self.assertIsNone(spring_a_items.value)
        spring_b_weight = next(r for r in rows
                               if r.season == '暖季B' and r.metric == 'weight')
        self.assertEqual(spring_b_weight.scope, 'joint')
        self.assertIsNone(spring_b_weight.value)
        self.assertEqual(spring_b_weight.upper, 6.0)


class FutureSeasonTests(unittest.TestCase):
    def test_adding_a_qualified_note_does_not_double_count_overlap(self):
        import dataclasses
        records = Pipeline.records
        notes = list(records.notes) + [dataclasses.replace(
            records.note('N5'), id='N99', visit='V3',
            text='对岸渡台查24分钟，一个瓶。桥对岸查28分钟，未见瓶。',
        )]
        records2 = dataclasses.replace(records, notes=tuple(notes))
        parses = parse_all(records2)
        obs, bags, unresolved = build_observations(records2, parses)
        states = build_states(records2, obs, unresolved, parses)
        comparisons = compare_all(records2, states)
        right = [c for c in comparisons if c.round_id == 'R1' and c.bank == '右岸'][0]
        # 同一段被同季两场走访覆盖时，共有范围仍是段长，不累计成两倍
        self.assertLessEqual(right.overlap_m, 260)


class BulletinTests(unittest.TestCase):
    def review(self, bid):
        return review_bulletin(Pipeline.records, Pipeline.states, Pipeline.comparisons,
                               Pipeline.parses, Pipeline.bags, bid)

    def test_weight_bulletin_loses_basis_and_names_replacements(self):
        review = self.review('B2')
        self.assertEqual(review.verdict, '需更正')
        statuses = {c.claim: c.status for c in review.claims}
        self.assertEqual(statuses['三段分别称重'], 'loses_basis')
        self.assertIn('两岸小计', review.replace_together)
        self.assertIn('总计', review.replace_together)
        self.assertIn('8公斤', review.erratum)
        self.assertIn('6公斤', review.erratum)

    def test_item_bulletin_cannot_claim_decline_without_range(self):
        for bid in ('B1', 'B3'):
            review = self.review(bid)
            self.assertEqual(review.verdict, '需更正')
            self.assertTrue(review.erratum)
            self.assertIn('范围', review.erratum)

    def test_report_returns_ranges_quotes_and_erratum_per_part(self):
        report = build_report(
            Pipeline.records, Pipeline.parses, Pipeline.states, Pipeline.observations,
            Pipeline.bags, Pipeline.unresolved, Pipeline.comparisons, season='暖季B',
        )
        self.assertTrue(any('堤顶' in line for rn in report.range_notes for line in rn.lines()))
        text = render_text(report)
        for needle in ('范围说明', '未决差异', '须一并替换', '更正稿', '手记'):
            self.assertIn(needle, text)
        # 原话可回查
        self.assertEqual(report.quotes['N2'], Pipeline.records.note('N2').text)

    def test_erratum_is_addressed_to_the_original_part(self):
        for bid in ('B1', 'B2', 'B3'):
            review = self.review(bid)
            self.assertTrue(review.erratum, bid)
            self.assertEqual(review.part, Pipeline.records.bulletin(bid).part)
            # 更正稿必须回到原栏目和原话，而不是只给结论
            self.assertTrue(review.range_explanation)
            self.assertTrue(review.cited_notes)
            for note_id in review.cited_notes:
                self.assertIn(note_id, {n.id for n in Pipeline.records.notes})

    def test_erratum_packet_is_claimed_by_original_part_and_lists_totals(self):
        report = build_report(
            Pipeline.records, Pipeline.parses, Pipeline.states, Pipeline.observations,
            Pipeline.bags, Pipeline.unresolved, Pipeline.comparisons, season='暖季B',
        )
        by_part = {e.part: e for e in report.errata}
        self.assertEqual(set(by_part), {'岸段件数', '两岸小计', '右岸件数'})
        weight_packet = by_part['两岸小计']
        self.assertTrue(weight_packet.must_replace_together)
        self.assertIn('总计', weight_packet.dependent_totals)
        self.assertIn('8公斤', ' '.join(weight_packet.replacement))
        # B2 是重量栏，不能再把件数栏的"仍可引用"贴过来
        self.assertNotIn('件数栏可以继续引用', ' '.join(weight_packet.still_usable))

    def test_reassessment_separates_what_still_holds_from_what_fails(self):
        report = build_report(
            Pipeline.records, Pipeline.parses, Pipeline.states, Pipeline.observations,
            Pipeline.bags, Pipeline.unresolved, Pipeline.comparisons, season='暖季B',
        )
        holds = ' '.join(report.reassessment.still_holds)
        loses = ' '.join(report.reassessment.loses_basis)
        self.assertIn('6 公斤', holds)
        self.assertIn('分别称重', loses)
        self.assertTrue(report.reassessment.unchanged_columns)


if __name__ == '__main__':
    unittest.main()
