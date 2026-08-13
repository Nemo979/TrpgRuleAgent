import unittest
from pathlib import Path

from trpg_app.answer_evaluation import load_cases
from trpg_app.complex_planner import (
    MAX_PLAN_TASKS,
    PlanValidationError,
    build_complex_plan,
    parse_plan,
)
from trpg_app.query_decomposition import decompose_query, route_query
from trpg_app.synthesis_contract import (
    SynthesisCheckKind,
    assess_synthesis_contract,
    build_synthesis_contract,
)


class ComplexPlannerTest(unittest.TestCase):
    def test_builds_bounded_topological_plan_for_complex_goal(self) -> None:
        query = (
            "我想规划6到12级法师兼职战士，必须保持指定法术环级；"
            "先核对兼职造成的施法进度变化，再根据是否满足环级决定兼职等级，"
            "并比较战士专长与装备收益。"
        )
        route = route_query(query)
        plan = build_complex_plan(query, route, decompose_query(query, route))

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertLessEqual(len(plan.tasks), MAX_PLAN_TASKS)
        self.assertEqual(plan.tasks[-1].type.value, "selection")
        evidence_queries = [task.query for task in plan.tasks if task.requires_evidence]
        self.assertEqual(len(evidence_queries), len(set(evidence_queries)))
        seen = set()
        for task in plan.tasks:
            self.assertTrue(set(task.depends_on).issubset(seen))
            seen.add(task.id)

    def test_simple_query_does_not_create_plan(self) -> None:
        query = "借机攻击是什么？"
        route = route_query(query)
        self.assertIsNone(build_complex_plan(query, route, decompose_query(query, route)))

    def test_planner_candidate_queries_are_unique_and_bounded(self) -> None:
        cases = load_cases(
            Path("rulepacks/pathfinder-1e/evals/complex-task-answer-cases.jsonl")
        )
        for case in cases:
            query = case.turns[0].query
            route = route_query(query)
            plan = build_complex_plan(query, route, decompose_query(query, route))
            self.assertIsNotNone(plan, case.id)
            assert plan is not None
            evidence_queries = [
                task.query for task in plan.tasks if task.requires_evidence
            ]
            self.assertEqual(
                len(evidence_queries),
                len(set(evidence_queries)),
                case.id,
            )
            self.assertLessEqual(len(plan.tasks), MAX_PLAN_TASKS)

    def test_synthesis_contract_covers_risky_claim_dimensions(self) -> None:
        cases = load_cases(
            Path("rulepacks/pathfinder-1e/evals/complex-task-answer-cases.jsonl")
        )
        contracts = {}
        for case in cases:
            query = case.turns[0].query
            route = route_query(query)
            plan = build_complex_plan(query, route, decompose_query(query, route))
            assert plan is not None
            contract = build_synthesis_contract(query, plan)
            contracts[case.id] = {check.kind for check in contract.checks}

        self.assertIn(
            SynthesisCheckKind.FEAT_ELIGIBILITY,
            contracts["conditional-feat-chain"],
        )
        self.assertIn(
            SynthesisCheckKind.EQUIPMENT_STAT,
            contracts["conditional-armor-casting"],
        )
        self.assertIn(
            SynthesisCheckKind.SPELL_PROGRESSION,
            contracts["conditional-spell-combination"],
        )
        self.assertIn(
            SynthesisCheckKind.LEVEL_ARITHMETIC,
            contracts["conditional-multiclass-casting"],
        )

    def test_unspecified_spell_level_requires_branches_not_an_assumption(self) -> None:
        query = (
            "我想规划6到12级法师兼职战士，必须保持指定法术环级；"
            "先核对兼职造成的施法进度变化，再决定兼职等级。"
        )
        route = route_query(query)
        plan = build_complex_plan(query, route, decompose_query(query, route))
        assert plan is not None
        contract = build_synthesis_contract(query, plan)

        self.assertEqual(contract.unresolved_fields, ("目标法术环级",))
        readiness = assess_synthesis_contract(contract, plan, ())
        self.assertEqual(readiness[0].status, "requires_user_branching")

    def test_rejects_cycles_unknown_capabilities_and_task_overflow(self) -> None:
        base = {
            "goal": "规划角色",
            "planner_version": 1,
            "tasks": [
                {
                    "id": "t1",
                    "type": "rule_research",
                    "query": "职业规则",
                    "domain": "class",
                    "depends_on": [],
                }
            ],
        }
        self.assertEqual(parse_plan(base).tasks[0].id, "t1")

        forbidden = {**base, "tasks": [{**base["tasks"][0], "query": "curl https://x"}]}
        with self.assertRaises(PlanValidationError):
            parse_plan(forbidden)

        unknown_capability = {
            **base,
            "tasks": [{**base["tasks"][0], "model": "mimo-v2.5"}],
        }
        with self.assertRaises(PlanValidationError):
            parse_plan(unknown_capability)

        cycle = {
            **base,
            "tasks": [
                {**base["tasks"][0], "depends_on": ["t2"]},
                {
                    **base["tasks"][0],
                    "id": "t2",
                    "depends_on": ["t1"],
                },
            ],
        }
        with self.assertRaises(PlanValidationError):
            parse_plan(cycle)

        overflow = {
            **base,
            "tasks": [
                {**base["tasks"][0], "id": f"t{index}"}
                for index in range(1, MAX_PLAN_TASKS + 2)
            ],
        }
        with self.assertRaises(PlanValidationError):
            parse_plan(overflow)


if __name__ == "__main__":
    unittest.main()
