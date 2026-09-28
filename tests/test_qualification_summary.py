"""Requested expensive tests cannot turn skipped XML into release evidence."""
import importlib.util
import json
from pathlib import Path
import pytest


@pytest.mark.parametrize("xml,passed", [
    (None,False), ("<broken>",False),
    ("<testsuite><testcase><skipped/></testcase></testsuite>",False),
    ("<testsuite/>",False),
    ("<testsuite>"+'<testcase/>'*4+"</testsuite>",True),
    ("<testsuite>"+'<testcase/>'*3+"</testsuite>",False),
])
def test_aeps_accounting(tmp_path,xml,passed):
    spec=importlib.util.spec_from_file_location("summary",Path(__file__).resolve().parents[1]/"scripts/summarize-qualification.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/"commands.json").write_text(json.dumps([{"name":"aeps","exit_code":0}]))
    if xml is not None:(tmp_path/"aeps.xml").write_text(xml)
    assert module.summarize(tmp_path)["passed"] is passed
