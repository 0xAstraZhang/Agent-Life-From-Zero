"""Browser-independent regressions for Stage 6 (full Chromium suite lives in the tutorial)."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def code(tmp_path, monkeypatch):
    extractor = load('extract_456', ROOT / 'scripts/check_tutorials_456.py')
    extractor.extract(6, tmp_path)
    ba = load('stage6_browser_agent', tmp_path / 'browser_agent.py')
    fake = load('stage6_fake_llm', tmp_path / 'fake_llm.py')
    monkeypatch.chdir(tmp_path)
    return ba, fake


def test_same_origin_post_is_denied(code):
    ba, _ = code
    session = ba.BrowserSession(ba.Policy({'lab.example'}), Path('runs'))
    outcome = []
    route = SimpleNamespace(
        request=SimpleNamespace(url='https://lab.example/save', method='POST', is_navigation_request=lambda: False),
        abort=lambda reason: outcome.append(reason), fallback=lambda: outcome.append('allowed'))
    session._guard(route)
    assert outcome == ['blockedbyclient']


@pytest.fixture
def browserless(code, monkeypatch):
    ba, fake = code
    class Session:
        def __init__(self, *args, **kwargs):
            self.blocked = []
            self.page_texts = {'https://lab.example/data': 'OD600 is 1.37'}
        def __enter__(self): return self
        def __exit__(self, *exc): pass
        def log(self, *args): pass
        def observe(self): return 'page'
        open_url = click = type_text = scroll = observe
    monkeypatch.setattr(ba, 'BrowserSession', Session)
    return ba, fake


def test_quote_cannot_claim_an_unseen_source(browserless):
    ba, fake = browserless
    client = fake.FakeLLM([
        fake.call('finish', answer='wrong', source_url='https://unseen.example/', quote='OD600 is 1.37'),
        fake.call('finish', answer='correct', source_url='https://lab.example/data', quote='OD600 is 1.37')])
    result = ba.run_browser_agent('query', {'lab.example'}, client=client)
    assert result.answer == 'correct' and result.steps == 2


def test_malformed_first_arguments_are_reported(browserless):
    ba, fake = browserless
    class Malformed(fake.FakeLLM):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if len(self.requests) == 1:
                response.choices[0].message.tool_calls[0].function.arguments = '{'
            return response
    client = Malformed([fake.call('observe'), fake.call('finish', answer='done', source_url='', quote='')])
    result = ba.run_browser_agent('query', {'lab.example'}, client=client)
    assert result.status == 'done'
    assert 'JSONDecodeError' in client.requests[-1]['messages'][-1]['content']
