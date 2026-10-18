"""Check publishable files (or the exact Git index), without printing secret values."""
import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {
    'data', 'runtime', 'models', 'cache', 'runs', 'logs', 'raw_files', 'benchmarks',
    'evaluation', 'experiments', 'review_artifacts', 'implement-stage', 'slides',
    'node_modules', 'dist', '__pycache__', '.agents', '.aris', '.hf_cache',
    'test-results', 'playwright-report', 'results',
}
PRIVATE_SUFFIXES = {
    '.mp4', '.mov', '.avi', '.mkv', '.webm', '.jpg', '.jpeg', '.png', '.gif',
    '.webp', '.wav', '.mp3', '.sqlite', '.sqlite3', '.db', '.key', '.pem',
    '.header', '.safetensors', '.onnx', '.pt', '.pth', '.gguf', '.bin',
    '.zip', '.tar', '.gz', '.7z', '.bundle', '.pdf', '.pptx', '.xlsx', '.docx',
    '.jsonl', '.csv', '.log', '.pyc', '.patch',
}
PATTERNS = {
    'host-specific home path': re.compile('/' + r'(?:raid/home|home/user\d+|Users)/'),
    'private tunnel hostname': re.compile(r'\b[a-z0-9.-]+\.(?:ts\.net|trycloudflare\.com|ngrok(?:-free)?\.app)\b', re.I),
    'private key material': re.compile('-----BEGIN ' + r'(?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'provider credential': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-proj-[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})\b'),
    'embedded image or video': re.compile(r'data:(?:image|video)/[^;\s]+;base64,[A-Za-z0-9+/=]{100,}'),
}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def files(staged):
    if staged:
        for entry in filter(None, git('ls-files', '--stage', '-z').decode().split('\0')):
            meta, name = entry.split('\t', 1)
            mode, oid, stage = meta.split()
            if stage != '0':
                raise ValueError('Resolve merge conflicts before publication')
            yield name, mode, git('cat-file', 'blob', oid)
    else:
        names = set(filter(None, git('ls-files', '-co', '--exclude-standard', '-z').decode().split('\0')))
        for name in sorted(names):
            path = ROOT / name
            if path.is_symlink():
                yield name, '120000', b''
            elif path.is_file():
                yield name, '100644', path.read_bytes()


def issues(name, mode, content):
    path = PurePosixPath(name)
    parts = {p.lower() for p in path.parts}
    if mode not in {'100644', '100755'}:
        yield 'symlink or submodule'
    if parts & PRIVATE_DIRS or any(p.startswith('.venv') for p in parts):
        yield 'private or generated directory'
    if path.suffix.lower() in PRIVATE_SUFFIXES or '.sqlite' in path.name or '.db-' in path.name:
        yield 'private or binary artifact'
    if (path.name.startswith('.env') and path.name != '.env.example') or 'credentials' in path.name.lower():
        yield 'local credentials/configuration'
    if len(content) > 3_000_000:
        yield 'file exceeds 3 MB source limit'
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        yield 'non-text file'
        return
    if '\0' in text:
        yield 'binary content'
    for label, pattern in PATTERNS.items():
        if pattern.search(text):
            yield label


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true')
    parser.add_argument('--gitleaks', action='store_true', help='Scan only the publication candidates with installed Gitleaks')
    args = parser.parse_args()
    candidates = list(files(args.staged))
    failures = [(name, issue) for name, mode, content in candidates for issue in issues(name, mode, content)]
    if failures:
        for name, issue in failures:
            print(f'{name}: {issue}')
        return 1
    print(f'Publication file check passed: {len(candidates)} files.')
    if args.gitleaks:
        if not shutil.which('gitleaks'):
            parser.error('Install gitleaks first')
        with tempfile.TemporaryDirectory(prefix='publication-scan-') as folder:
            for name, _, content in candidates:
                target = Path(folder) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            return subprocess.run(['gitleaks', 'dir', folder, '--redact', '--no-banner',
                                   '--config', str(ROOT / '.gitleaks.toml')]).returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
