"""Python 3.9 owner-only replacement of the existing staging promotion pin.

No credential reads, SSH changes, worker replacement, database or Docker actions.
The active automation state is preserved. Host apply still needs signed GitHub
workload identity and an actual protected deployment-security owner review.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

ROOT = Path('/opt/gamerhq-staging')
CONTROL = Path('/etc/gamerhq-staging')
OLD = '9c1a6f4bca0f46a3f34ac57830e45e356e3a4b03'
WEB_BASE = '9525f9cfd8afcb086ea5ce6a5287c0dd65987ca1'
LEGACY = '92db47042d58055cb0debdfd83f11f8508e19809'
PREVIOUS_HOST = '17858f47480e914956dd241b9f37a328b4c6ac2b'
PREVIOUS_PIN = {'commit': PREVIOUS_HOST,
    'archiveSha256': '77504247b70dee58c6f7d982eb4d65e50218a7fccb3581cd7e5723799fe888d9',
    'workflowSha256': '83e80648461ad899ed9a2fdcff18145039c2cdd5a2045fd211daf2c7443ad05d'}
HOST = '29ecf575f3d865b0f32a9d82adf1b06d7969cdc9'
ARCHIVE = '8d4286c5596963fe10b8f36da163bbe3de12d6b2512673f5b54535145ad424bd'
MODULES = ('__init__.py', 'policy.py', 'oidc.py', 'backend.py', 'bridge.py', 'host_promotion.py')
CATALOG_CONTROLLER = '11e4062beb4e22f57669becbd68797be2099185a'
FAILED_CONFIGURATION_CONTROLLER = '0931e16e85db6024b3142aacc072f7721ec28e2d'
SKILL_UPDATE_BACKUP = '1b01158008ff4839988a97911d31a1c9.sqlite'
CONFIGURATION_HOST = '89709625d9ea0c74c2b731436d1d13c9dcb453c4'
CONFIGURATION_ARCHIVE = 'b4155457b84179aad5263da8d9dda63fd83751e9de29a0c3c34aafbecf964a2c'
ACTIVE_CONFIGURATION_CONTROLLER = '4b15a48574f7c6ff3cc20f441afbc75160fd0bfb'
MANAGED_MESSAGES_HOST = '6734b756685d90de8e56ddc299560ec13625300e'
MANAGED_MESSAGES_ARCHIVE = '5f22a55a8d13d8a48fcb040d308bba240ba8d805939d9b51017a5f26efb368eb'
ACTIVE_MANAGED_MESSAGES_CONTROLLER = 'ff519f698f973b056eeae0b9d026a71dbf8fbaae'
STRUCTURE_HOST = 'de6447a25d36dc002070b2f0e94b4df0dcc9ef0b'
STRUCTURE_ARCHIVE = '260c8f886d7485bd6fafa7701ac9021d07d4cf1d8cff81c8387034d00731faf6'
ACTIVE_STRUCTURE_CONTROLLER = 'aefa82516ee227c4281bb65e08a16a083f1f7460'
BULK_STRUCTURE_HOST = 'c4985b0df53fffad7b83790b257368ac243c013a'
BULK_STRUCTURE_ARCHIVE = 'ad7db7f7c85f0e5ea94439148d8c63e5651dfdbc0080a677036f4afc6e2a73b8'
ACTIVE_BULK_STRUCTURE_CONTROLLER = '890cdfe91607bf3c47c015a65e2061f18ff1d865'
BULK_RECOVERY_HOST = '96855865b746c214f4197b9f2808cbae1c21b7ff'
BULK_RECOVERY_ARCHIVE = '4b6eb69666f4268ac715cd94797b35139c917dd5a074bb5a8a5d082386ade37f'
ACTIVE_BULK_RECOVERY_CONTROLLER = 'b7753c60461bd4281758b66b0cb2b6d1687b22be'
BULK_PREVIEW_HOST = '1539eb13cb242a765ea89130c28b0438ce8dcb48'
BULK_PREVIEW_ARCHIVE = '1faa932bc6c4878256e09444b0f2a7c8ac327a4f57bfe580d4a24baaba0b501a'
ACTIVE_BULK_PREVIEW_CONTROLLER = '8eea84e96a706b82e1ffb66a70b6e3c6fd01bd64'
BULK_PERMISSIONS_HOST = '71e1fcbb0cf3904365d1ed020a866b50755f58e4'
BULK_PERMISSIONS_ARCHIVE = '8636420eacd2f106b6b7a2b4ded497a02bfa29ac50af41122711f351b0d7aa0b'
ACTIVE_BULK_PERMISSIONS_CONTROLLER = '7576f8a95003bac40cc007e786a776efb4ddd470'
LOCALIZED_REUSE_HOST = '77b3d36361bf31a66c6fc4bc6898815cd5e96467'
LOCALIZED_REUSE_ARCHIVE = 'd52af3f62120b4f50e2b1bc6a7674238f2d6d094fc065e7f6eaad1e0b2cd5be4'
ACTIVE_LOCALIZED_REUSE_CONTROLLER = '79584661eaf013ae60bb049ab1e4c58c9c0bcb98'
SERVER_TEMPLATES_HOST = '20397fb4a82d1da80f89a9bcaf53ca8a6885775d'
SERVER_TEMPLATES_ARCHIVE = 'f1a158164094840a7c5039038c6d1dceb8625ea131285cb0fb9e219d2b8772b3'
ACTIVE_SERVER_TEMPLATES_CONTROLLER = 'f3a617827237d01d51c7b65df67bc3ff3d6f5eca'
TEMPLATE_RECOVERY_HOST = 'abdc719f09d097d10b0318a684679947eda61800'
TEMPLATE_RECOVERY_ARCHIVE = 'e8039fe10cc775de18fb802a9624cda2608ecf1c4c4369d582f0557701f7ba5c'
ACTIVE_TEMPLATE_RECOVERY_CONTROLLER = '7164199a73f27da292e942b3dd63224a8591230d'
TEMPLATE_STAFF_HOST = '812772cfa96cea52f5dfbc28a42272f8ce00b35a'
TEMPLATE_STAFF_ARCHIVE = '3cd5d011ccb7ba69f5b7325e61ab061468c436d84d787af66032ed9408be0b2c'
ACTIVE_TEMPLATE_STAFF_CONTROLLER = 'a0be89758149c30d03aa4209c0a92519a79461b3'
SETUP_REPORTS_HOST = 'ff66cfa9cc21240dc51ccdec81c2f128ab027191'
SETUP_REPORTS_ARCHIVE = 'b382c51bdebcc4978fde09cc2a78d8e84f6d37219d0395b10a56e95ec2680846'
ACTIVE_SETUP_REPORTS_CONTROLLER = 'e9f2e72807b1be1bd0df1ef01fee3af448d3969c'
REPORT_INTERACTION_HOST = '2fdf143c1a9582e8789fa853a36c757dc105e414'
REPORT_INTERACTION_ARCHIVE = '1b8155f3fb759bec7de41e9737c7ec4b13807d0b003a81268d25ef9a128a3534'
CATALOG_SOURCES = {
    'catalog_preview.py': 'tools/staging_automation/catalog_preview.py',
    'catalog_import.py': 'tools/game_catalog_import.py',
    'catalog_manifest.json': 'data/game_catalog_expansion.json',
}


def configuration_pin(policy, *, commit, previous_workflow, workflow, catalog_workflow,
                      managed_messages=False, structure=False, bulk_structure=False, bulk_recovery=False, bulk_preview=False, bulk_permissions=False, localized_reuse=False, server_templates=False, template_recovery=False, template_staff=False, setup_reports=False, report_interaction=False):
    catalog_digest = hashlib.sha256(catalog_workflow).hexdigest()
    if 'webDeployment' in policy or policy.get('catalogPreviewWorkflowSha256') != catalog_digest:
        raise ValueError('EXISTING_CATALOG_POLICY_CHANGED')
    if report_interaction:
        previous_host, previous_archive = SETUP_REPORTS_HOST, SETUP_REPORTS_ARCHIVE
        target_host, target_archive = REPORT_INTERACTION_HOST, REPORT_INTERACTION_ARCHIVE
    elif setup_reports:
        previous_host, previous_archive = TEMPLATE_STAFF_HOST, TEMPLATE_STAFF_ARCHIVE
        target_host, target_archive = SETUP_REPORTS_HOST, SETUP_REPORTS_ARCHIVE
    elif template_staff:
        previous_host, previous_archive = TEMPLATE_RECOVERY_HOST, TEMPLATE_RECOVERY_ARCHIVE
        target_host, target_archive = TEMPLATE_STAFF_HOST, TEMPLATE_STAFF_ARCHIVE
    elif template_recovery:
        previous_host, previous_archive = SERVER_TEMPLATES_HOST, SERVER_TEMPLATES_ARCHIVE
        target_host, target_archive = TEMPLATE_RECOVERY_HOST, TEMPLATE_RECOVERY_ARCHIVE
    elif server_templates:
        previous_host, previous_archive = LOCALIZED_REUSE_HOST, LOCALIZED_REUSE_ARCHIVE
        target_host, target_archive = SERVER_TEMPLATES_HOST, SERVER_TEMPLATES_ARCHIVE
    elif localized_reuse:
        previous_host, previous_archive = BULK_PERMISSIONS_HOST, BULK_PERMISSIONS_ARCHIVE
        target_host, target_archive = LOCALIZED_REUSE_HOST, LOCALIZED_REUSE_ARCHIVE
    elif bulk_permissions:
        previous_host, previous_archive = BULK_PREVIEW_HOST, BULK_PREVIEW_ARCHIVE
        target_host, target_archive = BULK_PERMISSIONS_HOST, BULK_PERMISSIONS_ARCHIVE
    elif bulk_preview:
        previous_host, previous_archive = BULK_RECOVERY_HOST, BULK_RECOVERY_ARCHIVE
        target_host, target_archive = BULK_PREVIEW_HOST, BULK_PREVIEW_ARCHIVE
    elif bulk_recovery:
        previous_host, previous_archive = BULK_STRUCTURE_HOST, BULK_STRUCTURE_ARCHIVE
        target_host, target_archive = BULK_RECOVERY_HOST, BULK_RECOVERY_ARCHIVE
    elif bulk_structure:
        previous_host, previous_archive = STRUCTURE_HOST, STRUCTURE_ARCHIVE
        target_host, target_archive = BULK_STRUCTURE_HOST, BULK_STRUCTURE_ARCHIVE
    elif structure:
        previous_host, previous_archive = MANAGED_MESSAGES_HOST, MANAGED_MESSAGES_ARCHIVE
        target_host, target_archive = STRUCTURE_HOST, STRUCTURE_ARCHIVE
    elif managed_messages:
        previous_host, previous_archive = CONFIGURATION_HOST, CONFIGURATION_ARCHIVE
        target_host, target_archive = MANAGED_MESSAGES_HOST, MANAGED_MESSAGES_ARCHIVE
    else:
        previous_host, previous_archive = HOST, ARCHIVE
        target_host, target_archive = CONFIGURATION_HOST, CONFIGURATION_ARCHIVE
    previous_pin = {'commit': previous_host, 'archiveSha256': previous_archive,
                    'workflowSha256': hashlib.sha256(previous_workflow).hexdigest()}
    pin = {'commit': target_host, 'archiveSha256': target_archive,
           'workflowSha256': hashlib.sha256(workflow).hexdigest()}
    expected = pin if policy.get('controllerCommit') == commit else previous_pin
    if policy.get('hostPromotion') != expected:
        raise ValueError('EXISTING_HOST_POLICY_CHANGED')
    return pin


def trusted(path, uid=0, private=False, limit=262144):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or info.st_mode & (0o077 if private else 0o022) or info.st_size > limit:
            raise ValueError('PROTECTED_FILE_REQUIRED')
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise ValueError('PROTECTED_FILE_REQUIRED')
        return data
    finally:
        os.close(fd)


def directory(path, gid=None):
    if not path.exists():
        if gid is None:
            raise ValueError('PROTECTED_PARENT_REQUIRED')
        path.mkdir(mode=0o750)
        os.chown(path, 0, gid)
        path.chmod(0o750)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('PROTECTED_PARENT_REQUIRED')


def atomic(path, body, gid):
    directory(path.parent)
    label = None
    if path.exists() or path.is_symlink():
        trusted(path)
        try: label = os.getxattr(path, 'security.selinux', follow_symlinks=False)
        except OSError: pass
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.host-extension-', delete=False) as output:
        temporary = Path(output.name)
        os.fchown(output.fileno(), 0, gid); os.fchmod(output.fileno(), 0o640)
        if label: os.setxattr(output.fileno(), 'security.selinux', label)
        output.write(body); output.flush(); os.fsync(output.fileno())
    try:
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if temporary.exists(): temporary.unlink()


def skill_update_backup_mode():
    """Report metadata only. Never open the known unsafe backup."""
    backup_root = ROOT / 'backups'
    try:
        directory_info = backup_root.lstat()
        if not stat.S_ISDIR(directory_info.st_mode):
            return None
        info = (backup_root / SKILL_UPDATE_BACKUP).lstat()
    except OSError:
        return None
    return format(stat.S_IMODE(info.st_mode), '04o') if stat.S_ISREG(info.st_mode) else None


def extension(commit, approval, *, web_hostname=None, previous_web_controller=None,
              catalog_preview=False, configuration_preview=False, configuration_recovery=False,
              managed_messages=False, structure=False, bulk_structure=False, bulk_recovery=False, bulk_preview=False, bulk_permissions=False, localized_reuse=False, server_templates=False, template_recovery=False, template_staff=False, setup_reports=False, report_interaction=False):
    import pwd
    web = web_hostname is not None
    if sum((web, catalog_preview, configuration_preview, configuration_recovery, managed_messages, structure, bulk_structure, bulk_recovery, bulk_preview, bulk_permissions, localized_reuse, server_templates, template_recovery, template_staff, setup_reports, report_interaction)) > 1:
        raise ValueError('SELECT_ONE_EXTENSION')
    configuration_preview = configuration_preview or configuration_recovery or managed_messages or structure or bulk_structure or bulk_recovery or bulk_preview or bulk_permissions or localized_reuse or server_templates or template_recovery or template_staff or setup_reports or report_interaction
    expected_approval = ('ENROLL STAGING REPORT INTERACTION FIX' if report_interaction else
                         'ENROLL STAGING COPYABLE SETUP REPORTS' if setup_reports else
                         'ENROLL STAGING TEMPLATE STAFF ACCESS' if template_staff else
                         'ENROLL STAGING SERVER TEMPLATE RECOVERY' if template_recovery else
                         'ENROLL STAGING SERVER TEMPLATES' if server_templates else
                         'ENROLL STAGING LOCALIZED CHANNEL REUSE' if localized_reuse else
                         'ENROLL STAGING BULK PERMISSION FIX' if bulk_permissions else
                         'ENROLL STAGING BULK PREVIEW REPORT' if bulk_preview else
                         'ENROLL STAGING BULK SETUP RECOVERY' if bulk_recovery else
                         'ENROLL STAGING BULK STRUCTURE SETUP' if bulk_structure else
                         'ENROLL STAGING STRUCTURE PHASES' if structure else
                         'ENROLL STAGING MANAGED MESSAGE PREVIEW' if managed_messages else
                         'INSTALL STAGING PROMOTION FIX WITH RECOVERY HELD' if configuration_recovery else
                         'ENROLL STAGING CONFIGURATION PREVIEW' if configuration_preview else
                         'ENROLL STAGING CATALOG PREVIEW' if catalog_preview else
                         'ENROLL ISOLATED STAGING WEB' if web else 'REPLACE REVIEWED STAGING HOST PIN')
    if os.geteuid() != 0 or approval != expected_approval:
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    if web and (not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}', web_hostname)
                or web_hostname.endswith(('.local', '.localhost', '.test', '.invalid', '.example'))):
        raise ValueError('VERIFIED_WEB_HOSTNAME_REQUIRED')
    if previous_web_controller is not None and not web:
        raise ValueError('OWNER_APPROVAL_REQUIRED')
    previous_commit = (ACTIVE_SETUP_REPORTS_CONTROLLER if report_interaction else
                       ACTIVE_TEMPLATE_STAFF_CONTROLLER if setup_reports else
                       ACTIVE_TEMPLATE_RECOVERY_CONTROLLER if template_staff else
                       ACTIVE_SERVER_TEMPLATES_CONTROLLER if template_recovery else
                       ACTIVE_LOCALIZED_REUSE_CONTROLLER if server_templates else
                       ACTIVE_BULK_PERMISSIONS_CONTROLLER if localized_reuse else
                       ACTIVE_BULK_PREVIEW_CONTROLLER if bulk_permissions else
                       ACTIVE_BULK_RECOVERY_CONTROLLER if bulk_preview else
                       ACTIVE_BULK_STRUCTURE_CONTROLLER if bulk_recovery else
                       ACTIVE_STRUCTURE_CONTROLLER if bulk_structure else
                       ACTIVE_MANAGED_MESSAGES_CONTROLLER if structure else
                       ACTIVE_CONFIGURATION_CONTROLLER if managed_messages else
                       FAILED_CONFIGURATION_CONTROLLER if configuration_recovery else
                       CATALOG_CONTROLLER if configuration_preview else
                       (previous_web_controller or WEB_BASE) if web or catalog_preview else OLD)
    previous_host = (SETUP_REPORTS_HOST if report_interaction else
                     TEMPLATE_STAFF_HOST if setup_reports else
                     TEMPLATE_RECOVERY_HOST if template_staff else
                     SERVER_TEMPLATES_HOST if template_recovery else
                     LOCALIZED_REUSE_HOST if server_templates else
                     BULK_PERMISSIONS_HOST if localized_reuse else
                     BULK_PREVIEW_HOST if bulk_permissions else
                     BULK_RECOVERY_HOST if bulk_preview else
                     BULK_STRUCTURE_HOST if bulk_recovery else
                     STRUCTURE_HOST if bulk_structure else MANAGED_MESSAGES_HOST if structure else
                     CONFIGURATION_HOST if managed_messages else
                     HOST if configuration_preview else PREVIOUS_HOST)
    if not all(re.fullmatch('[0-9a-f]{40}', value) for value in (commit, previous_commit)):
        raise ValueError('IMMUTABLE_SOURCE_REQUIRED')
    account = pwd.getpwnam('gamerhq-staging-bridge')
    uid, gid = account.pw_uid, account.pw_gid
    for parent in (ROOT, CONTROL, ROOT / 'automation-controller'):
        directory(parent)
    policy_path = CONTROL / 'automation-policy.json'
    original_policy = trusted(policy_path)
    policy = json.loads(original_policy)
    if policy.get('controllerCommit') not in (previous_commit, commit) or policy.get('legacyWorkerCommit') != LEGACY or policy.get('repository') != 'Duy-Phan96/GamerHQ':
        raise ValueError('REVIEWED_ENROLLMENT_REQUIRED')
    if catalog_preview and 'webDeployment' in policy:
        raise ValueError('REVIEWED_ENROLLMENT_REQUIRED')
    if web and previous_commit != WEB_BASE and policy.get('webDeployment', {}).get('hostname') != web_hostname:
        raise ValueError('EXISTING_WEB_ORIGIN_CHANGED')
    entry = ROOT / 'automation-controller/entry.py'
    old_entry = trusted(entry)
    revisions = ROOT / 'automation-controller/revisions'
    previous = revisions / previous_commit
    destination = revisions / commit
    for parent in (revisions, previous, previous / 'staging_automation'):
        directory(parent)

    def launcher(path):
        return ("import sys\nsys.path[:0]=[" + repr(str(path)) + ",'/opt/gamerhq-staging/deployment-worker']\n"
                "from staging_automation.bridge import entry\nentry()\n").encode()

    previous_launch, launch = launcher(previous), launcher(destination)
    if policy['controllerCommit'] == previous_commit and (
            old_entry != previous_launch or
            not (web or catalog_preview or configuration_preview) and policy.get('hostPromotion') != PREVIOUS_PIN):
        raise ValueError('INSTALLED_PIN_MISMATCH')
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': '/dev/null',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
    with tempfile.TemporaryDirectory(prefix='gamerhq-host-extension-') as temp:
        def git(*args):
            result = subprocess.run(['git', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', '-C', temp, *args], env=env, capture_output=True, check=True, timeout=180)
            if len(result.stdout) > 262144:
                raise ValueError('SOURCE_LIMIT')
            return result.stdout
        git('init', '--quiet'); git('remote', 'add', 'origin', 'https://github.com/Duy-Phan96/GamerHQ.git')
        for source in (commit, previous_commit): git('fetch', '--quiet', '--depth=1', 'origin', source)
        if git('show', commit + ':tools/extend_staging_host_promotion.py') != Path(__file__).read_bytes():
            raise ValueError('VERIFIED_INSTALLER_SOURCE_MISMATCH')
        blobs = {name: git('show', commit + ':tools/staging_automation/' + name) for name in MODULES}
        target_host = (REPORT_INTERACTION_HOST if report_interaction else
                       SETUP_REPORTS_HOST if setup_reports else
                       TEMPLATE_STAFF_HOST if template_staff else
                       TEMPLATE_RECOVERY_HOST if template_recovery else
                       SERVER_TEMPLATES_HOST if server_templates else
                       LOCALIZED_REUSE_HOST if localized_reuse else
                       BULK_PERMISSIONS_HOST if bulk_permissions else
                       BULK_PREVIEW_HOST if bulk_preview else
                       BULK_RECOVERY_HOST if bulk_recovery else
                       BULK_STRUCTURE_HOST if bulk_structure else STRUCTURE_HOST if structure else
                       MANAGED_MESSAGES_HOST if managed_messages else
                       CONFIGURATION_HOST if configuration_preview else HOST)
        if f'COMMIT = {target_host!r}'.encode() not in blobs['host_promotion.py'].splitlines():
            raise ValueError('REVIEWED_HOST_SOURCE_MISMATCH')
        if web:
            blobs['web.py'] = git('show', commit + ':tools/staging_automation/web.py')
            web_pin = json.loads(git('show', commit + ':deploy/staging/web-candidate.json'))
            web_workflow = git('show', commit + ':.github/workflows/staging-web.yml')
            web_pin.update(hostname=web_hostname, workflowSha256=hashlib.sha256(web_workflow).hexdigest())
        if catalog_preview:
            blobs.update({name: git('show', commit + ':' + path) for name, path in CATALOG_SOURCES.items()})
            catalog_workflow = git('show', commit + ':.github/workflows/staging-catalog-preview.yml')
        # Same canonical state machine, not a second deployment implementation.
        blobs['deployment.py'] = git('show', commit + ':hosts/gamerhq/skill_update_controller.py')
        for name in ('staging-automation.yml', 'staging-automation-activate.yml'):
            body = git('show', commit + ':.github/workflows/' + name)
            key = 'routineWorkflowSha256' if name == 'staging-automation.yml' else 'securityWorkflowSha256'
            if hashlib.sha256(body).hexdigest() != policy[key]:
                raise ValueError('EXISTING_WORKFLOW_POLICY_CHANGED')
        workflow = git('show', commit + ':.github/workflows/staging-host-promotion.yml')
        if (web or catalog_preview) and policy.get('hostPromotion') != {'commit': HOST, 'archiveSha256': ARCHIVE, 'workflowSha256': hashlib.sha256(workflow).hexdigest()}:
            raise ValueError('EXISTING_HOST_POLICY_CHANGED')
        previous_modules = (*MODULES, 'web.py') if web and previous_commit != WEB_BASE else MODULES
        changed_modules = {'host_promotion.py'} if managed_messages or structure or bulk_structure or bulk_recovery or bulk_preview or bulk_permissions or localized_reuse or server_templates or template_recovery or template_staff or setup_reports or report_interaction else (
            {'host_promotion.py', 'backend.py'} if configuration_recovery else {'host_promotion.py', 'policy.py'})
        for name in previous_modules:
            installed = trusted(previous / 'staging_automation' / name)
            if installed != git('show', previous_commit + ':tools/staging_automation/' + name):
                raise ValueError('INSTALLED_SOURCE_MISMATCH')
            if configuration_preview and name not in changed_modules and blobs[name] != installed:
                raise ValueError('UNRELATED_CONTROLLER_SOURCE_CHANGED')
        installed_deployment = trusted(previous / 'staging_automation/deployment.py')
        if installed_deployment != git('show', previous_commit + ':hosts/gamerhq/skill_update_controller.py'):
            raise ValueError('INSTALLED_SOURCE_MISMATCH')
        if configuration_preview:
            if blobs['deployment.py'] != installed_deployment:
                raise ValueError('UNRELATED_CONTROLLER_SOURCE_CHANGED')
            previous_workflow = git('show', previous_commit + ':.github/workflows/staging-host-promotion.yml')
            catalog_workflow = git('show', previous_commit + ':.github/workflows/staging-catalog-preview.yml')
            if configuration_recovery and previous_workflow != workflow:
                raise ValueError('EXISTING_HOST_POLICY_CHANGED')
            # Recovery selects corrected code for the already-installed pin.
            # Compare the current policy to that exact pin even on the first run.
            pin_policy = {**policy, 'controllerCommit': commit} if configuration_recovery else policy
            pin = configuration_pin(pin_policy, commit=commit, previous_workflow=previous_workflow,
                                    workflow=workflow, catalog_workflow=catalog_workflow,
                                    managed_messages=managed_messages, structure=structure,
                                    bulk_structure=bulk_structure, bulk_recovery=bulk_recovery, bulk_preview=bulk_preview, bulk_permissions=bulk_permissions,
                                    localized_reuse=localized_reuse, server_templates=server_templates, template_recovery=template_recovery, template_staff=template_staff, setup_reports=setup_reports, report_interaction=report_interaction)
            for name, path in CATALOG_SOURCES.items():
                installed = trusted(previous / 'staging_automation' / name)
                if installed != git('show', previous_commit + ':' + path):
                    raise ValueError('INSTALLED_SOURCE_MISMATCH')
                blobs[name] = installed
    # Existing sandbox/SSH launcher still invokes the same entry file. Only its
    # root-owned import target and a narrowly scoped root-owned policy change.
    new_policy = {**policy, 'controllerCommit': commit}
    if catalog_preview:
        new_policy['catalogPreviewWorkflowSha256'] = hashlib.sha256(catalog_workflow).hexdigest()
    elif web:
        new_policy['webDeployment'] = web_pin
    elif configuration_preview:
        new_policy['hostPromotion'] = pin
    else:
        new_policy['hostPromotion'] = {'commit': HOST, 'archiveSha256': ARCHIVE, 'workflowSha256': hashlib.sha256(workflow).hexdigest()}
    oauth_path = CONTROL / 'web-oauth.json'
    oauth_body = None
    if web and not (oauth_path.exists() or oauth_path.is_symlink()):
        import getpass
        import secrets
        import sys
        if not sys.stdin.isatty():
            raise ValueError('TRUSTED_INTERACTIVE_SESSION_REQUIRED')
        client_id = input('Dedicated staging Discord OAuth application ID: ').strip()
        client_secret = getpass.getpass('Dedicated staging Discord OAuth client secret: ')
        if not re.fullmatch(r'[0-9]{17,20}', client_id) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', client_secret):
            raise ValueError('STAGING_OAUTH_CONFIGURATION_REJECTED')
        oauth_body = json.dumps({'discordClientId': client_id, 'discordClientSecret': client_secret,
                                 'sessionSecret': secrets.token_urlsafe(40)}).encode()
    lock_path = ROOT / 'worker/operation.lock'
    parent_info = lock_path.parent.lstat()
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != uid or parent_info.st_gid != gid or parent_info.st_mode & 0o002:
        raise ValueError('PRIVATE_WORKER_DIRECTORY_REQUIRED')
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        # Some existing systemd user managers create 0666 locks. The enclosing
        # private staging directory protects this non-data inode; acquire the
        # exact owned single-link inode, then tighten it without path-based chmod.
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid or info.st_nlink != 1:
            raise ValueError('EXISTING_OPERATION_LOCK_REQUIRED')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if configuration_recovery or managed_messages or structure or bulk_structure or bulk_recovery or bulk_preview or bulk_permissions or localized_reuse or server_templates or template_recovery or template_staff or setup_reports or report_interaction:
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('PRIVATE_OPERATION_LOCK_REQUIRED')
        else:
            os.fchmod(fd, 0o600)
        # Re-read under the same lock used by every existing deployment operation.
        if trusted(policy_path) != original_policy or trusted(entry) != old_entry:
            raise ValueError('CONCURRENT_POLICY_CHANGE_REJECTED')
        state = json.loads(trusted(ROOT / 'worker/automation-state.json', uid, private=True))
        if configuration_recovery:
            if state.get('recoveryRequired') is not True:
                raise ValueError('RECOVERY_LATCH_REQUIRED')
        elif state.get('recoveryRequired'):
            raise ValueError('REVIEWED_RECOVERY_REQUIRED')
        if not state.get('enabled') or not state.get('bootstrapVerified') or not re.fullmatch('sha256:[0-9a-f]{64}', state.get('approvedImage', '')):
            raise ValueError('ACTIVE_STAGING_REQUIRED')
        if managed_messages or structure or bulk_structure or bulk_recovery or bulk_preview or bulk_permissions or localized_reuse or server_templates or template_recovery or template_staff or setup_reports or report_interaction:
            profile = json.loads(trusted(CONTROL / 'settings/profile.json', uid, private=True))
            if (profile.get('environment') != 'staging' or profile.get('applicationHostCommit') != previous_host
                    or profile.get('baseImage') != state['approvedImage']):
                raise ValueError('INSTALLED_STAGING_BASELINE_REQUIRED')
        # A crash after policy replace but before entry replace is recoverable
        # only with the same exact root-owned old/new entry and immutable policy.
        if policy['controllerCommit'] == commit and (policy != new_policy or old_entry not in (launch, previous_launch)):
            raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
        directory(destination, gid); directory(destination / 'staging_automation', gid)
        for name, body in blobs.items():
            target = destination / 'staging_automation' / name
            if target.exists() and trusted(target) != body:
                raise ValueError('IMMUTABLE_EXTENSION_CHANGED')
            atomic(target, body, gid)
        try:
            if web:
                if oauth_body is not None:
                    if oauth_path.exists() or oauth_path.is_symlink():
                        raise ValueError('CONCURRENT_POLICY_CHANGE_REJECTED')
                    atomic(oauth_path, oauth_body, gid)
                else:
                    trusted(oauth_path, limit=4096)
            atomic(policy_path, (json.dumps(new_policy, indent=2) + '\n').encode(), gid)
            atomic(entry, launch, gid)
            if Path('/sys/fs/selinux/enforce').exists():
                paths = [revisions, entry, policy_path] + ([oauth_path] if web else [])
                subprocess.run(['/usr/sbin/restorecon', '-RF', *map(str, paths)], check=True, capture_output=True, timeout=30)
        except Exception:
            atomic(entry, old_entry, gid); atomic(policy_path, original_policy, gid)
            raise
    finally:
        os.close(fd)
    import syslog
    syslog.openlog('gamerhq-staging-security', syslog.LOG_PID, syslog.LOG_AUTHPRIV)
    if catalog_preview:
        receipt = {'operation': 'enroll-staging-catalog-preview', 'state': 'CATALOG_PREVIEW_ENROLLED',
                   'controllerCommit': commit, 'databaseChanged': False, 'containersChanged': False,
                   'productionChanged': False}
        syslog.syslog(syslog.LOG_NOTICE, json.dumps(receipt))
        return receipt
    if web:
        receipt = {'operation': 'enroll-isolated-staging-web', 'state': 'WEB_ENROLLED', 'controllerCommit': commit,
                   'webCommit': web_pin['commit'], 'hostname': web_hostname, 'containersChanged': False,
                   'gatewayChanged': False, 'productionChanged': False}
        syslog.syslog(syslog.LOG_NOTICE, json.dumps(receipt))
        return receipt
    if configuration_recovery:
        receipt = {'operation': 'install-staging-promotion-fix', 'state': 'FIX_INSTALLED_RECOVERY_HELD',
                   'controllerCommit': commit, 'approvedHostCommit': target_host, 'recoveryRequired': True,
                   'catalogPreviewPolicyPreserved': True, 'promotionPinChanged': False,
                   'skillUpdateBackupMode': skill_update_backup_mode(),
                   'databaseChanged': False, 'containersChanged': False, 'gatewayRestarted': False,
                   'discordResourcesChanged': False, 'productionChanged': False}
        syslog.syslog(syslog.LOG_NOTICE, json.dumps(receipt))
        return receipt
    syslog.syslog(syslog.LOG_NOTICE, json.dumps({'operation': 'replace-staging-host-promotion-pin', 'controllerCommit': commit,
        'approvedHostCommit': target_host, 'state': 'PIN_INSTALLED', 'productionChanged': False}))
    return {'operation': 'replace-staging-host-promotion-pin', 'state': 'PIN_INSTALLED', 'controllerCommit': commit,
        'previousHostCommit': previous_host,
        'approvedHostCommit': target_host,
        'archiveSha256': new_policy['hostPromotion']['archiveSha256'],
        'catalogPreviewPolicyPreserved': configuration_preview, 'hostPromotionRequiresProtectedOwnerReview': True,
        'automationEnabled': bool(state.get('enabled')), 'gatewayStarted': False, 'productionChanged': False,
        'workerSourceChanged': False, 'sshPolicyChanged': False, 'credentialsChanged': False,
        'databaseChanged': False, 'containersChanged': False, 'deploymentGatesChanged': False, 'operationLockMode': '0600'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--commit', required=True); parser.add_argument('--approval', required=True)
    parser.add_argument('--web-hostname')
    parser.add_argument('--previous-web-controller')
    parser.add_argument('--catalog-preview', action='store_true')
    parser.add_argument('--configuration-preview', action='store_true')
    parser.add_argument('--configuration-preview-recovery', action='store_true')
    parser.add_argument('--managed-message-preview', action='store_true')
    parser.add_argument('--structure-phases', action='store_true')
    parser.add_argument('--bulk-structure-setup', action='store_true')
    parser.add_argument('--bulk-setup-recovery', action='store_true')
    parser.add_argument('--bulk-preview-report', action='store_true')
    parser.add_argument('--bulk-permission-fix', action='store_true')
    parser.add_argument('--localized-channel-reuse', action='store_true')
    parser.add_argument('--server-templates', action='store_true')
    parser.add_argument('--server-template-recovery', action='store_true')
    parser.add_argument('--template-staff-access', action='store_true')
    parser.add_argument('--copy-setup-reports', action='store_true')
    parser.add_argument('--report-interaction-fix', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(extension(args.commit, args.approval, web_hostname=args.web_hostname,
                                   previous_web_controller=args.previous_web_controller, catalog_preview=args.catalog_preview,
                                   configuration_preview=args.configuration_preview,
                                   configuration_recovery=args.configuration_preview_recovery,
                                   managed_messages=args.managed_message_preview, structure=args.structure_phases,
                                   bulk_structure=args.bulk_structure_setup,
                                   bulk_recovery=args.bulk_setup_recovery, bulk_preview=args.bulk_preview_report,
                                   bulk_permissions=args.bulk_permission_fix,
                                   localized_reuse=args.localized_channel_reuse, server_templates=args.server_templates,
                                   template_recovery=args.server_template_recovery, template_staff=args.template_staff_access, setup_reports=args.copy_setup_reports, report_interaction=args.report_interaction_fix), indent=2))
    except Exception as error:
        code = str(error) if re.fullmatch('[A-Z_]{1,80}', str(error)) else 'HOST_EXTENSION_REJECTED'
        raise SystemExit(json.dumps({'extensionError': code, 'productionChanged': False})) from None
