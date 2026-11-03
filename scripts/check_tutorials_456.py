"""Extract and run the complete offline tests embedded in Stage 4–6.

Install dependencies into the Python environment used to run this script:
  uv pip install pytest openai pydantic langgraph pyyaml 'mcp[cli]>=2,<3' playwright
  playwright install chromium
Run: python scripts/check_tutorials_456.py [--stage 4 5 6]
No model credentials or live model calls are used. Stage 6 uses local mocked pages.
Optional: set TUTORIAL_CHROMIUM_EXECUTABLE to use an already-installed Chromium.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    4: [None, None, None, 'multi_agent.py', 'fake_llm.py', 'test_multi_agent.py', 'graph_version.py'],
    5: ['skills/research-report/scripts/check_report.py', 'skill_runtime.py', 'smoke_test.py',
        'fake_llm.py', 'test_skills.py', 'lab_mcp_server.py'],
    6: [None, 'browser_agent.py', 'fake_llm.py', 'test_browser_agent.py', None],
}


def extract(stage: int, target: Path) -> None:
    source = next((ROOT / 'docs').glob(f'Stage {stage} *.md')).read_text(encoding='utf-8')
    blocks = re.findall(r'^```python\n(.*?)^```$', source, re.M | re.S)
    if len(blocks) != len(FILES[stage]):
        raise ValueError(f'Stage {stage}: code block layout changed; update FILES mapping')
    for name, content in zip(FILES[stage], blocks):
        if name:
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding='utf-8')
    if stage == 5:
        for pattern, name in [
            (r'^```markdown\n(---\nname: research-report.*?)^```$', 'SKILL.md'),
            (r'^```markdown\n(# 报告标题.*?)^```$', 'references/report-template.md'),
        ]:
            match = re.search(pattern, source, re.M | re.S)
            if match is None:
                raise ValueError(f'Missing Stage 5 asset {name}')
            out = target / 'skills/research-report' / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(match[1], encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', nargs='+', type=int, choices=FILES, default=list(FILES))
    args = parser.parse_args()
    failed = False
    with tempfile.TemporaryDirectory(prefix='tutorial-check-456-') as temp:
        for stage in args.stage:
            directory = Path(temp) / f'stage{stage}'
            directory.mkdir()
            extract(stage, directory)
            if stage == 6 and os.environ.get('TUTORIAL_CHROMIUM_EXECUTABLE'):
                (directory / 'conftest.py').write_text(
                    "import os\nimport pytest\nfrom playwright.sync_api import BrowserType\n"
                    "@pytest.fixture(autouse=True)\ndef installed_browser(monkeypatch):\n"
                    "    original = BrowserType.launch\n"
                    "    def launch(self, **kwargs):\n"
                    "        kwargs['executable_path'] = os.environ['TUTORIAL_CHROMIUM_EXECUTABLE']\n"
                    "        return original(self, **kwargs)\n"
                    "    monkeypatch.setattr(BrowserType, 'launch', launch)\n",
                    encoding='utf-8',
                )
            print(f'\nStage {stage}', flush=True)
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=directory)
            failed |= result.returncode != 0
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
