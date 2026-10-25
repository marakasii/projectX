"""Explicit synthetic live probe through existing ChatGPT CLI; never an API fallback."""
import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ai import CodexCLIProvider


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='visapilot-probe-') as directory:
        provider = CodexCLIProvider(enabled=True, model=args.model, temporary_root=Path(directory))
        result = await provider.generate('cover_letter', {'sentences': [
            'My name is ALEXEI SAMPLEOV.', 'I plan to visit FR for tourism.',
            'My intended travel dates are 2026-12-01 to 2026-12-10.',
        ]}, {'type': 'object', 'properties': {'paragraph_order': {
            'type': 'array', 'items': {'type': 'integer', 'enum': [0, 1, 2]},
            'minItems': 3, 'maxItems': 3}},
            'required': ['paragraph_order'], 'additionalProperties': False})
        print(result.model_dump_json())
        return 0 if result.status == 'available' else 2


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
