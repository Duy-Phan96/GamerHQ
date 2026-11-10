"""Disposable CI overlay/provenance/UI probe; no credentials, DB or Gateway."""
import argparse
import json
import tempfile

from tools.staging_automation.backend import HostBackend
from tools.staging_automation.host_promotion import ARCHIVE_SHA256, COMMIT, build_overlay, source_commit


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--base-image', required=True); args = parser.parse_args()
    from hosts.gamerhq.skill_deployment_docker import IMAGE
    if not IMAGE.fullmatch(args.base_image):
        raise ValueError('Immutable disposable image required')
    with tempfile.TemporaryDirectory(prefix='host-promotion-ci-') as root:
        backend = HostBackend({'workRoot': root}, {'hostPromotion': {'commit': COMMIT, 'archiveSha256': ARCHIVE_SHA256}})
        image = build_overlay(backend, args.base_image)
        probe = '''import asyncio,json
from bot import bot
from cogs.event_editor import SkillEventEditor,WeekdayView,EventPreviewView
from cogs.lfg import BuilderDateSelect,BuilderTimeSelect,BuilderPlayersSelect,WeekdayToggle
from skill_runtime import ManagementField,ManagementFieldOption
from services.server_configuration_preview import preview
from tools.game_catalog_import import manifest
from inspect import iscoroutinefunction
assert iscoroutinefunction(preview) and iscoroutinefunction(bot.setup_hook) and not bot.is_ready()
assert len(manifest('/app/data/game_catalog_expansion.json')) == 70
async def check():
 fields=(ManagementField(key='weekdays',label='Weekdays',type='multi_select',config_path='weekdays',options=tuple(ManagementFieldOption(str(i),name) for i,name in enumerate(('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')))),)
 draft=dict(name='Offline fixture',kind='recurring',weekdays=[0,2,4],startDate='2030-01-07',localTime='19:00',timezone='Europe/Berlin',maxPlayers=5,visibility='private')
 view=SkillEventEditor(10,fields,draft)
 assert len(view.children)==9
 assert isinstance(view.children[0],BuilderDateSelect) and isinstance(view.children[1],BuilderTimeSelect) and isinstance(view.children[2],BuilderPlayersSelect)
 assert len([b for b in WeekdayView(view).children if isinstance(b,WeekdayToggle)])==7
 assert EventPreviewView(view).confirm.disabled
 assert 'Every Monday, Wednesday, Friday' in view.content()
 print(json.dumps(dict(compactEditor='PASS',configurationPreview='PASS',startupImports='PASS',weekdayControls=7,gatewayStarted=False)))
asyncio.run(check())
'''
        result = backend.run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--user', '10001:10001', '--memory', '256m', '--cpus', '1', '--pids-limit', '64',
            '--entrypoint', 'python', image, '-c', probe])
        assert all(json.loads(result)[check] == 'PASS' for check in ('compactEditor', 'configurationPreview', 'startupImports'))
        assert source_commit(backend, image) == COMMIT
        print(json.dumps({'hostCommit': COMMIT, 'archiveSha256': ARCHIVE_SHA256, 'overlay': 'PASS',
            'compactEditor': 'PASS', 'configurationPreview': 'PASS', 'startupImports': 'PASS', 'gatewayStarted': False, 'credentialsUsed': False, 'databaseMounted': False}))


if __name__ == '__main__': main()
