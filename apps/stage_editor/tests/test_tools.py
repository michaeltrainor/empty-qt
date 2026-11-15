"""Validation and the Python console against a live document."""

from __future__ import annotations

from pxr import Usd, UsdGeom

from stage_editor.document import StageDocument
from stage_editor.interpreter import EditorApi, Interpreter
from stage_editor.validation import apply_fix, validate_stage


def test_validation_reports_stage_metadata_and_fixes_it() -> None:
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/Root")
    issues = validate_stage(stage)
    by_name = {issue.name: issue for issue in issues}
    assert "MissingMetersPerUnitMetadata" in by_name
    issue = by_name["MissingMetersPerUnitMetadata"]
    assert issue.severity == "Error"
    assert issue.paths == ["/"]
    if issue.fixable:
        assert apply_fix(stage, issue)
        names = {i.name for i in validate_stage(stage)}
        assert "MissingMetersPerUnitMetadata" not in names

    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    stage.SetDefaultPrim(stage.GetPrimAtPath("/Root"))
    assert not any(i.severity == "Error" for i in validate_stage(stage))


def test_console_runs_statements_against_the_document(qtbot) -> None:
    document = StageDocument()
    selection: list[str] = []
    api = EditorApi(document, lambda: list(selection), selection.__init__)
    console = Interpreter(api)
    qtbot.addWidget(console)

    console.execute("prim = api.stage.DefinePrim('/World/Probe', 'Sphere')")
    console.execute("api.select([prim.GetPath()])")
    assert selection == ["/World/Probe"]
    assert console.execute("for name in ('a', 'b'):")
    assert console.execute("    print(name.upper())")
    assert not console.execute("")
    console.execute("api.prim.GetTypeName()")
    console.execute("1 / 0")
    output = console.output
    assert "A\nB\n" in output
    assert "'Sphere'" in output
    assert "ZeroDivisionError" in output
    assert document.stage.GetPrimAtPath("/World/Probe").IsA(UsdGeom.Sphere)
