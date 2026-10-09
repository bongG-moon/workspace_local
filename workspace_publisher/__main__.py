"""Optional command-line entry; credentials are prompted and never arguments."""
import argparse
import getpass
import json
from pathlib import Path

from .core import Publisher
from .config import PublisherError, normalize


def main(argv=None):
    parser = argparse.ArgumentParser(description='AX Workspace 사내 GitLab 게시 도구 · Download ZIP에서도 빌드와 게시 가능')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('action', choices=('status', 'configure', 'check', 'preview-sync', 'sync', 'build', 'publish', 'deploy', 'source-publish', 'notes',
                                          'sdk-status', 'sdk-import', 'sdk-download'))
    parser.add_argument('--file', type=Path, help='sdk-import에서 선택할 사내의 .nupkg 파일')
    parser.add_argument('--url', help='sdk-download의 선택적 사내 HTTPS 주소. 생략하면 고정된 NuGet 주소 사용. 저장하지 않음')
    args = parser.parse_args(argv)
    if args.action == 'sdk-import' and args.file is None:
        parser.error('sdk-import에는 --file로 준비된 .nupkg 파일을 지정해 주세요.')
    if args.file is not None and args.action != 'sdk-import':
        parser.error('--file은 sdk-import에서만 사용합니다.')
    if args.url is not None and args.action != 'sdk-download':
        parser.error('--url은 sdk-download에서만 사용합니다.')
    publisher = Publisher(args.repo, emit=lambda event: print(event['message']))
    try:
        if args.action.startswith('sdk-'):
            from .sdk import inspect_sdk, import_sdk, download_sdk
            emit = lambda event: print(event['message'])
            if args.action == 'sdk-status':
                result = inspect_sdk(args.repo)
            elif args.action == 'sdk-import':
                result = import_sdk(args.repo, args.file, emit=emit)
            else:
                result = download_sdk(args.repo, args.url, emit=emit)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get('status') == 'ready' else 1
        config = publisher.load_config()
        if args.action == 'configure':
            source = publisher.inspect_source()
            archive = source.get('sourceKind') == 'archive'
            if archive:
                config['releaseTag'] = ''
                print('Download ZIP 모드: Git 없이 빌드·게시하고 구성 파일도 HTTPS로 프로젝트에 올릴 수 있습니다.')
            elif not config['releaseTag']:
                config['releaseTag'] = source['releaseTag']
            config['baseUrl'] = input(f"사내 GitLab HTTPS 서버 주소 [{config['baseUrl']}]: ").strip() or config['baseUrl']
            config['projectId'] = input(f"숫자 Project ID [{config['projectId']}]: ").strip() or config['projectId']
            config = normalize(config)
            print('변경 내용은 소스의 배포 기록에서 자동으로 채웠습니다. 편집은 배포 창에서 할 수 있습니다.')
            result = publisher.save_config(config)
        elif args.action == 'notes':
            result = publisher.release_notes()
        elif args.action == 'status':
            result = publisher.inspect_source()
        elif args.action == 'check':
            result = publisher.check_connection(config)
        elif args.action in ('preview-sync', 'sync'):
            result = publisher.preview_sync(config)
            if args.action == 'sync':
                print(result['summary'])
                if input('위 소스를 사내 저장소에 반영하려면 반영 입력: ').strip() != '반영':
                    return 1
                result = publisher.sync(config, result)
        elif args.action == 'build':
            result = publisher.build(config)
        else:
            token = getpass.getpass('게시용 토큰(저장하지 않음): ')
            try:
                if not token.strip():
                    raise PublisherError('게시용 토큰을 입력해 주세요.')
                if args.action == 'source-publish':
                    result = publisher.publish_source(config, token, token_kind=config['tokenKind'])
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 0
                result = (publisher.prepare_deploy(config) if args.action == 'deploy'
                          else publisher.load_last_build(config))
                if result is None:
                    raise PublisherError('먼저 build 명령으로 배포 파일을 만들어 주세요.')
                source_result = None
                if config['includeSource']:
                    source_result = publisher.publish_source(config, token, token_kind=config['tokenKind'], build_result=result)
                    if not isinstance(source_result, dict) or source_result.get('verified') is not True:
                        raise PublisherError('프로젝트 구성 파일 게시를 확인하지 못했습니다. 업데이트 채널은 게시하지 않았습니다.')
                result = publisher.publish(config, result, token, token_kind=config['tokenKind'])
                if source_result:
                    result = {**result, 'sourcePublication': source_result}
            finally:
                token = ''
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except PublisherError as exc:
        print(str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
