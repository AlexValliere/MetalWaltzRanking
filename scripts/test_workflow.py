"""Offline scope decisions and release-workflow safety invariants."""

from contextlib import redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

import workflow_plan


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")


def job(name):
    """Extract a top-level job for explicit structural checks, without a YAML dependency."""
    match = re.search(r"^  " + re.escape(name) + r":\n(.*?)(?=^  [\w-]+:\n|\Z)",
                      WORKFLOW, re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError("Missing workflow job: " + name)
    return match.group(1)


class WorkflowPlanTests(unittest.TestCase):
    def test_scheduled_collection_selects_all_ten_canonical_backends(self):
        plan = workflow_plan.collection_plan("schedule", 1, False)
        keys = plan["matrix"]["server"]
        self.assertTrue(plan["collect"])
        self.assertEqual(len(keys), 10)
        self.assertEqual(set(keys), workflow_plan.EXPECTED_BACKENDS)
        self.assertNotIn("SteamTEST", keys)

    def test_only_manual_affected_scope_selects_exact_three_mobile_backends(self):
        plan = workflow_plan.collection_plan("workflow_dispatch", 1, True, "affected-mobile")
        self.assertEqual(plan, {"collect": True,
                                "matrix": {"server": ["Bonn", "KOREA", "BANGKOK"]}})
        scheduled = workflow_plan.collection_plan("schedule", 1, False, "affected-mobile")
        self.assertEqual(set(scheduled["matrix"]["server"]), workflow_plan.EXPECTED_BACKENDS)

    def test_push_manual_no_fetch_and_all_reruns_never_collect(self):
        cases = [("push", 1, True), ("workflow_dispatch", 1, False)]
        cases += [(event, attempt, True) for event in ("schedule", "workflow_dispatch", "push")
                  for attempt in (2, 3)]
        for event, attempt, fetch in cases:
            with self.subTest(event=event, attempt=attempt, fetch=fetch):
                plan = workflow_plan.collection_plan(event, attempt, fetch)
                self.assertFalse(plan["collect"])
                self.assertEqual(set(plan["matrix"]["server"]), workflow_plan.EXPECTED_BACKENDS)

    def test_invalid_scope_attempt_or_fetch_is_rejected(self):
        for kwargs in ({"attempt": 0}, {"attempt": True}, {"attempt": "1"},
                       {"fetch": "true"}, {"scope": "Bonn"}, {"scope": "$(unreviewed)"}):
            values = dict(event="workflow_dispatch", attempt=1, fetch=True)
            values.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                workflow_plan.collection_plan(**values)

    def test_unreviewed_missing_or_duplicate_backend_directory_is_rejected(self):
        keys = sorted(workflow_plan.EXPECTED_BACKENDS)
        for bad in (keys[:-1], keys + ["SteamTEST"], keys[:-1] + [keys[0]]):
            with self.subTest(keys=bad), \
                    patch("workflow_plan.load_servers", return_value=[{"key": key} for key in bad]), \
                    self.assertRaises(ValueError):
                workflow_plan.collection_plan("schedule", 1, False)

    def test_main_writes_parseable_fixed_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            env = {"WORKFLOW_EVENT": "workflow_dispatch", "WORKFLOW_ATTEMPT": "1",
                   "WORKFLOW_FETCH": "true", "WORKFLOW_SCOPE": "affected-mobile",
                   "GITHUB_OUTPUT": str(output)}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(workflow_plan.main(), 0)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertEqual(set(values), {"collect", "matrix"})
        self.assertEqual(values["collect"], "true")
        self.assertEqual(json.loads(values["matrix"]), {"server": ["Bonn", "KOREA", "BANGKOK"]})

    def test_invalid_environment_emits_no_workflow_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            with patch.dict(os.environ, {"WORKFLOW_EVENT": "schedule", "WORKFLOW_ATTEMPT": "1",
                                         "WORKFLOW_FETCH": "yes", "GITHUB_OUTPUT": str(output)}, clear=True), \
                    redirect_stderr(StringIO()):
                self.assertEqual(workflow_plan.main(), 2)
            self.assertFalse(output.exists())


class WorkflowSafetyTests(unittest.TestCase):
    def test_single_account_jobs_are_sequential_and_do_not_cancel_other_servers(self):
        collect = job("collect")
        self.assertIn("      max-parallel: 1\n", collect)
        self.assertIn("      fail-fast: false\n", collect)
        self.assertIn("      matrix: ${{ fromJSON(needs.checks.outputs.matrix) }}", collect)
        self.assertNotRegex(collect, r"(?m)^    continue-on-error:")
        self.assertIn("  group: metal-waltz-rankings-publish\n  cancel-in-progress: false", WORKFLOW)

    def test_offline_success_and_current_attempt_are_required_before_credentials(self):
        collect = job("collect")
        self.assertIn("    needs: checks\n", collect)
        self.assertIn("needs.checks.result == 'success'", collect)
        self.assertIn("github.run_attempt == '1'", collect)
        self.assertIn("needs.checks.outputs.collect == 'true'", collect)
        self.assertIn("!cancelled()", collect)
        checks = job("checks")
        self.assertIn("github.repository == 'AlexValliere/MetalWaltzRanking'", checks)
        self.assertIn("github.ref == 'refs/heads/main'", checks)
        self.assertLess(checks.index("Run offline checks"), checks.index("id: plan"))
        self.assertNotIn("secrets.", checks)

    def test_each_runner_restores_validates_and_persists_before_reporting_failure(self):
        collect = job("collect")
        positions = [collect.index(value) for value in (
            "python scripts/snapshot_store.py restore", "id: collect", "python -m collector collect",
            "python scripts/build_site.py --check", "python scripts/snapshot_store.py save",
            "if: steps.collect.outcome == 'failure'", "exit 1")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("        continue-on-error: true\n", collect)
        self.assertIn('--server "$SERVER_KEY" --max-duration-seconds 19800', collect)
        self.assertIn("SERVER_KEY: ${{ matrix.server }}", collect)
        timeout = int(re.search(r"    timeout-minutes: (\d+)", collect).group(1))
        self.assertEqual(timeout, 350)
        self.assertGreaterEqual(timeout * 60 - 19800, 20 * 60)
        self.assertLess(timeout, 360)
        self.assertLessEqual(max(server["sessionSeconds"] for server in workflow_plan.load_servers()), 19800)

    def test_only_the_collection_step_receives_the_game_credentials(self):
        self.assertEqual(WORKFLOW.count("secrets.MW_COLLECTOR_USERNAME"), 1)
        self.assertEqual(WORKFLOW.count("secrets.MW_COLLECTOR_PASSWORD"), 1)
        collect = job("collect")
        secret = collect.index("secrets.MW_COLLECTOR_USERNAME")
        self.assertLess(collect.index("id: collect"), secret)
        self.assertLess(secret, collect.index("python -m collector collect"))
        for name in ("checks", "build", "deploy", "collection-status"):
            self.assertNotIn("MW_COLLECTOR_", job(name))

    def test_publication_waits_for_matrix_but_survives_failed_or_skipped_collection(self):
        build = job("build")
        self.assertIn("    needs: [checks, collect]", build)
        self.assertIn("if: ${{ !cancelled() && needs.checks.result == 'success' }}", build)
        self.assertNotIn("needs.collect.result == 'success'", build)
        positions = [build.index(value) for value in (
            "python scripts/snapshot_store.py restore", "python scripts/build_site.py\n",
            "python scripts/snapshot_store.py save", "uses: actions/upload-pages-artifact@")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("needs.build.result == 'success'", job("deploy"))

    def test_failure_rollup_uses_matrix_result_instead_of_matrix_outputs(self):
        status = job("collection-status")
        self.assertIn("    needs: [checks, collect, build, deploy]", status)
        self.assertIn("needs.collect.result == 'failure'", status)
        self.assertIn("!cancelled()", status)
        self.assertIn("exit 1", status)
        self.assertNotIn("outputs:", job("collect"))
        self.assertNotIn("needs.collect.outputs", WORKFLOW)

    def test_actions_remain_pinned_and_manual_scope_is_fixed(self):
        actions = re.findall(r"uses: ([^\s#]+)", WORKFLOW)
        self.assertTrue(actions)
        for action in actions:
            self.assertRegex(action, r"^actions/[a-z-]+@[0-9a-f]{40}$")
        self.assertIn("      collection_scope:\n", WORKFLOW)
        self.assertIn("        default: all\n        options:\n          - all\n          - affected-mobile", WORKFLOW)
        self.assertNotIn("${{ inputs.collection_scope }}", job("collect"))


if __name__ == "__main__":
    unittest.main()
