"""Extract and run the complete offline tests embedded in Stage 7–8.

Install dependencies in the Python environment used to run this script:
  uv pip install pytest openai httpx
Run: python scripts/check_tutorials_78.py [--stage 7 8]
No credentials, live model calls, real emails or repository file deletions are used.
Extracted files and each test workspace live in temporary directories.
This checks tutorial code, not real-model quality or GitHub Actions execution.
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
    7: [None, 'agent_under_test.py', 'eval_runner.py', 'demo_scripts.py',
        'fake_llm.py', 'test_eval.py'],
    8: ['src/lab_agent/config.py', 'src/lab_agent/agent.py', 'src/lab_agent/tools.py',
        'src/lab_agent/cli.py', 'src/lab_agent/__init__.py', None,
        'evals/run_evals.py', 'tests/fake_llm.py', 'tests/test_agent.py'],
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
    # Each chapter has exactly one JSON task-set block.
    tasks = re.findall(r'^```json\n(.*?)^```$', source, re.M | re.S)
    if len(tasks) != 1:
        raise ValueError(f'Stage {stage}: expected one task-set block')
    task_path = target / ('tasks.jsonl' if stage == 7 else 'evals/tasks.jsonl')
    task_path.write_text(tasks[0], encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', nargs='+', type=int, choices=FILES, default=list(FILES))
    args = parser.parse_args()
    failed = False
    with tempfile.TemporaryDirectory(prefix='tutorial-check-78-') as temp:
        for stage in args.stage:
            directory = Path(temp) / f'stage{stage}'
            directory.mkdir()
            extract(stage, directory)
            env = dict(os.environ)
            if stage == 8:
                env['PYTHONPATH'] = str(directory / 'src') + os.pathsep + env.get('PYTHONPATH', '')
            print(f'\nStage {stage}', flush=True)
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=directory, env=env)
            failed |= result.returncode != 0
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
