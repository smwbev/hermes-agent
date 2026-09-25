"""Proposed runner payload: never changes source/tests or detector behavior."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CANDIDATE = 'a031b84c863935d706a80041ca7f8668261fef20'
BASE = 'ec243785e41e7a12d47b099d9f747c781687f57a'
FILES = ['tests/hermes_cli/test_pip_install_detection.py', 'tests/hermes_cli/test_ensure_hermes_home_uid.py', 'tests/test_scratch_dir.py', 'tests/hermes_cli/test_config_provider_id_string.py', 'tests/hermes_cli/test_config_yaml_comment_preservation.py', 'tests/hermes_cli/test_config_set_list_values.py', 'tests/hermes_cli/test_config_validation.py', 'tests/hermes_cli/test_config_lock_free_cache_hit.py', 'tests/hermes_cli/test_platform_plugin_env_injection.py', 'tests/hermes_platform/test_facts.py', 'tests/hermes_platform/test_products.py', 'tests/hermes_platform/test_import_hygiene.py', 'tests/test_hermes_constants.py', 'tests/test_managed_runtime_resolution.py', 'tests/hermes_cli/test_config.py', 'tests/hermes_cli/test_config_save_integrity.py', 'tests/hermes_cli/test_config_unversioned_migration.py', 'tests/hermes_cli/test_config_edit_seed.py', 'tests/hermes_cli/test_config_guard_surfaces.py', 'tests/hermes_cli/test_config_lkg_backup.py', 'tests/agent/test_serves_routed_profile_pin.py', 'tests/agent/test_secret_scope_tier1_migration.py', 'tests/hermes_platform/test_resolver_core.py', 'tests/hermes_platform/test_resolver_app.py', 'tests/hermes_platform/test_declaration.py']
repo = Path.cwd()
out = repo / 'verification-evidence'
actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
assert actual == CANDIDATE, (actual, CANDIDATE)
assert not subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip()
out.mkdir(exist_ok=True)
os.umask(0o022)
tmp = Path(tempfile.mkdtemp(prefix='pr33494-', dir=os.environ['RUNNER_TEMP']))
for name in ('home', 'hermes', 'tmp'):
    (tmp / name).mkdir()
env = {k: v for k, v in os.environ.items()
       if not k.startswith(('HERMES_', 'PYTEST_', 'PYTHON'))
       and not any(s in k for s in ('TOKEN', 'API_KEY', 'SECRET', 'PASSWORD'))}
env.update(HOME=str(tmp/'home'), HERMES_HOME=str(tmp/'hermes'), TMPDIR=str(tmp/'tmp'),
           TMP=str(tmp/'tmp'), TEMP=str(tmp/'tmp'), PYTHONPATH=str(repo),
           PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
           TZ='UTC', LANG='C.UTF-8', PYTHONHASHSEED='0')
preflight_argv = [sys.executable, '-B', '-c',
    'import os,json; from pathlib import Path; from hermes_constants import _detect_container; '
    'c=_detect_container(); print(json.dumps(dict(container=c,dockerenv=Path("/.dockerenv").exists(),'
    'containerenv=Path("/run/.containerenv").exists(),uid=os.getuid()))); '
    'assert os.name=="posix" and not c, "Requires genuine non-container POSIX runner"']
argv = [sys.executable, '-B', '-m', 'pytest', '-p', 'no:cacheprovider', '-o', 'addopts=',
        '--basetemp', str(tmp/'pytest'), *FILES, '-v', '-rs', '--junitxml', str(out/'junit.xml')]
manifest = dict(candidate_sha=actual, base_sha=BASE, task_id='t_84e35a43', generation='cccec59a828e4e70a14c3beef3a9aec4',
    implementation_run_id=150, github_run_id=os.environ.get('GITHUB_RUN_ID'),
    github_run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'), workflow_sha=os.environ.get('GITHUB_SHA'),
    wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    umask='022', files=FILES, argv=argv, exit_code=None, status='running',
    preflight=dict(argv=preflight_argv, status='not_run', exit_code=None),
    pytest=dict(argv=argv, status='not_run', exit_code=None),
    file_sha256={p: hashlib.sha256((repo/p).read_bytes()).hexdigest() for p in FILES})


def save_manifest():
    # Replace atomically: interruption must not leave half a JSON document.
    pending = out / 'tests.json.tmp'
    pending.write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    pending.replace(out / 'tests.json')


for name in ('preflight.log', 'pytest.log'):
    (out / name).write_bytes(b'')
save_manifest()  # Provenance exists even when the real detector rejects this host.
try:
    for phase, command, timeout in (('preflight', preflight_argv, 60), ('pytest', argv, 900)):
        manifest[phase]['status'] = 'running'
        save_manifest()
        result = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout)
        (out / (phase + '.log')).write_bytes(result.stdout)
        print(result.stdout.decode(errors='replace'), flush=True)
        manifest[phase].update(status='passed' if result.returncode == 0 else 'failed',
                               exit_code=result.returncode)
        manifest['exit_code'] = result.returncode
        if result.returncode:
            manifest['status'] = phase + '_failed'
            break
    else:
        manifest['status'] = 'passed'
except subprocess.TimeoutExpired as exc:
    # stderr is merged into stdout; retain raw bytes (including partial UTF-8).
    captured = exc.output or b''
    if isinstance(captured, str):
        captured = captured.encode('utf-8')
    (out / (phase + '.log')).write_bytes(captured)
    print(captured.decode(errors='replace'), flush=True)
    manifest[phase].update(status='timeout', exit_code=None, timeout_seconds=exc.timeout)
    # 124 is the wrapper exit, not an invented child return code.
    manifest.update(status=phase + '_timeout', exit_code=124)
finally:
    integrity = subprocess.run(['git', 'diff', '--exit-code', CANDIDATE, '--'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (out / 'integrity.log').write_bytes(integrity.stdout)
    manifest['integrity_exit_code'] = integrity.returncode
    if integrity.returncode:
        manifest.update(status='integrity_failed', exit_code=integrity.returncode)
    manifest['log_sha256_by_file'] = {
        name: hashlib.sha256((out / name).read_bytes()).hexdigest()
        for name in ('preflight.log', 'pytest.log', 'integrity.log')}
    manifest['log_sha256'] = manifest['log_sha256_by_file']['pytest.log']
    save_manifest()
sys.exit(manifest['exit_code'])
