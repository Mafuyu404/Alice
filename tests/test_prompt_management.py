from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from kokoro.action import tool_spec
from kokoro.action import tools as action_tools
from kokoro.core import prompts as core_prompts
from kokoro.prompt import legacy
from kokoro.prompt import (
    PromptContext,
    PromptFragment,
    PromptManager,
    StrictRenderer,
    TemplateRenderError,
    discover_tool_prompt_specs,
    index_tool_prompt_specs,
    load_tool_prompt_spec,
    load_template,
    render_tool_catalog,
)
from kokoro.prompt.contracts import (
    LIFE_CONTEXT_COMPACT_SCENE,
    LIFE_JSON_REPAIR_SCENE,
    LIFE_PATCH_FALLBACK_SCENE,
    LIFE_TICK_SCENE,
)
from kokoro.prompt.registry import PromptRegistry


class PromptManagementTests(unittest.TestCase):
    def test_strict_renderer_rejects_missing_variables(self) -> None:
        renderer = StrictRenderer()

        with self.assertRaises(TemplateRenderError):
            renderer.render("hello {{ name }}", {})

    def test_strict_renderer_rejects_unused_variables(self) -> None:
        renderer = StrictRenderer()

        with self.assertRaises(TemplateRenderError):
            renderer.render("hello {{ name }}", {"name": "Alice", "extra": "unused"})

    def test_strict_renderer_allows_repeated_variables(self) -> None:
        renderer = StrictRenderer()

        text = renderer.render("{{ name }} sees {{ name }}", {"name": "Alice"})

        self.assertEqual(text, "Alice sees Alice")

    def test_core_prompts_facade_uses_strict_prompt_renderer(self) -> None:
        self.assertIn("{{ name }}", core_prompts.get("life_runtime.tick_system").replace("{name}", "{{ name }}"))

        with self.assertRaises(TemplateRenderError):
            core_prompts.format_prompt("life_runtime.tick_user", name="Only Name")

    def test_fragment_wraps_marker(self) -> None:
        fragment = PromptFragment(
            id="test.fragment",
            role="system",
            scope="global",
            template="hello {{ name }}",
            marker=("<test>", "</test>"),
            values=lambda ctx: {"name": ctx.character_name},
        )

        rendered = fragment.render(PromptContext(scene="test", character_name="Alice"), StrictRenderer())

        self.assertEqual(rendered.content, "<test>\nhello Alice\n</test>")

    def test_prompt_manager_renders_life_tick_from_legacy_prompt(self) -> None:
        manager = PromptManager()
        ctx = PromptContext(
            scene=LIFE_TICK_SCENE,
            character_id="test_role",
            character_name="Test Role",
            values={
                "inner_stream": "I am here.",
                "inner_stream_version": 3,
                "time_context": "Runtime elapsed: 10s",
                "context_digest": "one dense line",
                "tool_capabilities": "Registered action names: get_current_time",
                "event_batch": "new text",
                "pending_threads": "(none)",
                "tool_results_digest": "(none)",
            },
        )

        messages = manager.render(LIFE_TICK_SCENE, ctx)

        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn("<life_contract>", messages[0]["content"])
        self.assertIn("Test Role", messages[0]["content"])
        self.assertIn("I am here.", messages[1]["content"])
        self.assertIn("Registered action names", messages[1]["content"])
        self.assertIsNotNone(manager.last_trace)
        self.assertEqual(manager.last_trace.scene, LIFE_TICK_SCENE)

    def test_life_templates_are_filesystem_backed_strict_templates(self) -> None:
        base = load_template("life/base.md")
        tick = load_template("life/inner_stream_tick.md")
        compact = load_template("life/context_compact_user.md")

        self.assertIn("{{ name }}", base)
        self.assertIn("{{ inner_stream }}", tick)
        self.assertIn("{{ live_events }}", compact)
        self.assertNotIn("{name}", base)

    def test_life_prompt_preserves_action_plan_and_pending_thread_contracts(self) -> None:
        base = load_template("life/base.md")
        tick = load_template("life/inner_stream_tick.md")
        combined = base + "\n" + tick

        self.assertIn('"id":"a1","tool":"能力名"', combined)
        self.assertIn('"id":"a1 tool=能力名"', combined)
        self.assertIn("不要把能力名、等号、空格或参数塞进 id", combined)
        self.assertIn("pending_threads 是留给未来自己的自然牵挂", combined)
        self.assertIn("不要只在 notes 里说", combined)
        self.assertIn("写 inner_stream 时用“问题如何变了”", combined)
        self.assertNotIn('"tool":"search_web"', combined)
        self.assertNotIn("read_article", combined)
        self.assertNotIn("download_source_code", combined)

    def test_memory_templates_are_filesystem_backed_strict_templates(self) -> None:
        cognition = load_template("memory/cognition_evaluate_user.md")
        events = load_template("memory/events_extract_user.md")
        reflection = load_template("memory/reflection_system.md")

        self.assertIn("{{ existing }}", cognition)
        self.assertIn("{{ assistant_text }}", events)
        self.assertIn('"remember"', reflection)
        self.assertNotIn("{existing}", cognition)

    def test_migrated_memory_prompts_render_through_core_facade(self) -> None:
        rendered = core_prompts.format_prompt(
            "cognition.evaluate_user",
            existing="{}",
            conversation="hello",
            summary="none",
            memories="none",
            name="Alice",
        )
        emotion = core_prompts.format_prompt(
            "emotion.evaluate_user",
            current="情绪基调：（无）",
            user_name="Tester",
            user_text="hi",
            name="Alice",
            assistant_text="hello",
        )
        reflection = core_prompts.format_prompt(
            "inner_memory_reflection.system",
            name="Alice",
        )

        self.assertTrue(legacy.has_template_override("cognition.evaluate_user"))
        self.assertIn("hello", rendered)
        self.assertIn("Tester", emotion)
        self.assertIn('"remember"', reflection)

    def test_dialogue_vision_and_tool_templates_use_conventional_overrides(self) -> None:
        dialogue = load_template("dialogue/dialogue_orchestrator/planner_user.md")
        vision = load_template("vision/user_commands/screen_inspect_prompt.md")
        tool = load_template("tools/agent_guard/route.md")

        self.assertIn("{{ user_name }}", dialogue)
        self.assertIn("{{ user_text }}", vision)
        self.assertIn("{{ available_tools }}", tool)
        self.assertTrue(legacy.has_template_override("dialogue_orchestrator.planner_user"))
        self.assertTrue(legacy.has_template_override("user_commands.screen_inspect_prompt"))
        self.assertTrue(legacy.has_template_override("agent_guard.route"))

    def test_conventional_overrides_render_through_core_facade(self) -> None:
        screen_prompt = core_prompts.format_prompt(
            "user_commands.screen_inspect_prompt",
            user_text="看看这个页面",
        )
        tool_error = core_prompts.format_prompt(
            "tool_calling.tool_error",
            error="boom",
        )
        edge_error = core_prompts.format_prompt(
            "edge_cache.error_format",
            error="not ready",
        )

        self.assertIn("看看这个页面", screen_prompt)
        self.assertIn("boom", tool_error)
        self.assertIn("not ready", edge_error)

    def test_get_keeps_legacy_format_compatibility_for_overrides(self) -> None:
        prefix = core_prompts.get("scene.prefix")

        self.assertIn("{scene_name}", prefix)
        self.assertIn("测试场景", prefix.format(scene_name="测试场景"))

    def test_all_prompt_toml_strings_have_template_override(self) -> None:
        missing: list[str] = []
        for module, section in legacy.load().items():
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                if isinstance(value, str) and not legacy.has_template_override(f"{module}.{key}"):
                    missing.append(f"{module}.{key}")

        self.assertEqual(missing, [])

    def test_prompt_manager_writes_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = PromptManager()
            ctx = PromptContext(
                scene=LIFE_TICK_SCENE,
                character_name="Trace Role",
                trace_dir=str(Path(tmp) / "prompt_trace"),
                values={
                    "inner_stream": "trace stream",
                    "inner_stream_version": 0,
                    "time_context": "time",
                    "context_digest": "digest",
                    "tool_capabilities": "tools",
                    "event_batch": "events",
                    "pending_threads": "pending",
                    "tool_results_digest": "results",
                },
            )

            manager.render(LIFE_TICK_SCENE, ctx)

            self.assertTrue((Path(tmp) / "prompt_trace" / "trace.json").exists())
            self.assertTrue((Path(tmp) / "prompt_trace" / "context.json").exists())
            self.assertTrue((Path(tmp) / "prompt_trace" / "selected_fragments.json").exists())
            self.assertTrue((Path(tmp) / "prompt_trace" / "snapshots_before.json").exists())
            self.assertTrue((Path(tmp) / "prompt_trace" / "snapshots_after.json").exists())
            self.assertTrue((Path(tmp) / "prompt_trace" / "messages.json").exists())
            self.assertTrue((Path(tmp) / "prompt_trace" / "rendered_system.md").exists())
            self.assertTrue((Path(tmp) / "prompt_trace" / "rendered_user.md").exists())

    def test_prompt_manager_renders_life_support_scenes(self) -> None:
        manager = PromptManager()
        cases = [
            (
                LIFE_CONTEXT_COMPACT_SCENE,
                {
                    "time_context": "time",
                    "inner_stream": "stream",
                    "previous_digest": "previous",
                    "pending_threads": "pending",
                    "tool_results_digest": "tool result",
                    "live_events": "live",
                },
                "live",
            ),
            (
                LIFE_JSON_REPAIR_SCENE,
                {"parse_reason": "bad json", "raw_output": "{bad"},
                "bad json",
            ),
            (
                LIFE_PATCH_FALLBACK_SCENE,
                {"inner_stream": "stream", "raw_patch": "{\"x\":1}", "failure_reason": "target missing"},
                "target missing",
            ),
        ]
        for scene, values, expected in cases:
            with self.subTest(scene=scene):
                messages = manager.render(scene, PromptContext(scene=scene, values=values))

                self.assertEqual([message["role"] for message in messages], ["system", "user"])
                self.assertIn(expected, messages[-1]["content"])

    def test_prompt_registry_can_render_custom_scene(self) -> None:
        registry = PromptRegistry()
        registry.register(
            "custom",
            PromptFragment(
                id="custom.user",
                role="user",
                scope="runtime",
                template="value={{ value }}",
                values=lambda ctx: {"value": ctx.require("value")},
            ),
        )
        manager = PromptManager(registry=registry)

        messages = manager.render("custom", PromptContext(scene="custom", values={"value": "ok"}))

        self.assertEqual(messages, [{"role": "user", "content": "value=ok"}])

    def test_prompt_manager_skips_unchanged_snapshot_fragments(self) -> None:
        registry = PromptRegistry()
        registry.register(
            "snapshot_scene",
            PromptFragment(
                id="snapshot.env",
                role="system",
                scope="runtime",
                template="env={{ value }}",
                values=lambda ctx: {"value": ctx.require("value")},
                snapshot=lambda ctx: {"value": ctx.require("value")},
            ),
        )
        manager = PromptManager(registry=registry)
        ctx = PromptContext(scene="snapshot_scene", values={"value": "same"})

        first = manager.render("snapshot_scene", ctx)
        second = manager.render("snapshot_scene", ctx)

        self.assertEqual(first, [{"role": "system", "content": "env=same"}])
        self.assertEqual(second, [])
        assert manager.last_trace is not None
        self.assertTrue(manager.last_trace.fragments[-1]["skipped_by_diff"])

    def test_prompt_fragment_budget_truncates_and_reports_trace(self) -> None:
        registry = PromptRegistry()
        registry.register(
            "budget_scene",
            PromptFragment(
                id="budget.user",
                role="user",
                scope="runtime",
                template="abcdef",
                budget=3,
            ),
        )
        manager = PromptManager(registry=registry)

        messages = manager.render("budget_scene", PromptContext(scene="budget_scene"))

        self.assertEqual(messages, [{"role": "user", "content": "abc"}])
        assert manager.last_trace is not None
        self.assertTrue(manager.last_trace.fragments[-1]["truncated"])

    def test_tool_prompt_manifest_loader_keeps_prompts_inside_tool_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "search_web"
            tool.mkdir()
            (tool / "manifest.toml").write_text(
                "\n".join(
                    [
                        'id = "search_web"',
                        'name = "search_web"',
                        'description = "search public web information"',
                        "needs_prepare_llm = true",
                        'prepare_prompt = "prepare.md"',
                        'after_prompt = "after.md"',
                    ]
                ),
                encoding="utf-8",
            )
            (tool / "prepare.md").write_text("prepare query", encoding="utf-8")
            (tool / "after.md").write_text("summarize result", encoding="utf-8")

            spec = load_tool_prompt_spec(tool)
            specs = discover_tool_prompt_specs(tmp)

            self.assertIsNotNone(spec)
            assert spec is not None
            self.assertEqual(spec.id, "search_web")
            self.assertTrue(spec.needs_prepare_llm)
            self.assertEqual(spec.prepare_prompt, "prepare query")
            self.assertEqual(spec.after_prompt, "summarize result")
            self.assertEqual([item.id for item in specs], ["search_web"])

    def test_real_tool_prompt_manifests_are_discoverable_by_action_name(self) -> None:
        specs = discover_tool_prompt_specs(Path("kokoro") / "action" / "tools")
        index = index_tool_prompt_specs(specs)
        catalog = render_tool_catalog(specs, {"search_web", "say", "get_current_time"})
        detailed_catalog = render_tool_catalog(specs, {"search_web"}, include_stage_prompts=True)

        self.assertIn("search_web", index)
        self.assertIn("say", index)
        self.assertIn("get_current_time", index)
        self.assertTrue(index["search_web"].needs_prepare_llm)
        self.assertIn("query", index["search_web"].prepare_prompt)
        self.assertIn("- ", catalog)  # the catalog is short metadata, not full prepare prompts
        self.assertIn("search_web", catalog)
        self.assertIn("say", catalog)
        self.assertIn("get_current_time", catalog)
        self.assertNotIn("你在为 search_web 工具提炼搜索请求", catalog)
        self.assertIn("你在为 search_web 工具提炼搜索请求", detailed_catalog)
        self.assertIn("query 要保留当前注意力里的具体对象", detailed_catalog)

    def test_every_registered_action_has_tool_prompt_manifest_entry(self) -> None:
        registry = tool_spec.ActionToolRegistry()
        action_tools.register_all(registry)
        specs = discover_tool_prompt_specs(Path("kokoro") / "action" / "tools")
        index = index_tool_prompt_specs(specs)

        missing = sorted(action for action in registry.registered_actions() if action not in index)

        self.assertEqual(missing, [])

    def test_all_static_format_prompt_calls_match_template_variables(self) -> None:
        issues: list[str] = []
        for path in Path("kokoro").rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr == "format_prompt"):
                    continue
                if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                    continue
                prompt_path = node.args[0].value
                template = legacy.get(prompt_path, None)
                if not isinstance(template, str):
                    issues.append(f"{path}:{node.lineno} missing template {prompt_path}")
                    continue
                if any(keyword.arg is None for keyword in node.keywords):
                    continue
                required = legacy.variables_for_path(prompt_path)
                provided = {keyword.arg for keyword in node.keywords if keyword.arg is not None}
                missing = sorted(required - provided)
                extra = sorted(provided - required)
                if missing or extra:
                    issues.append(f"{path}:{node.lineno} {prompt_path} missing={missing} extra={extra}")

        self.assertEqual(issues, [])
if __name__ == "__main__":
    unittest.main()
