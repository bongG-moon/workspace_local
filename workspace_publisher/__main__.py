"""Optional command-line entry; credentials are prompted and never arguments."""
import argparse
import getpass
import json
from pathlib import Path

from .core import Publisher
from .config import PublisherError


def main(argv=None):
    parser = argparse.ArgumentParser(description='Company Workspace 사내 GitLab 게시 도구')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('action', choices=('status', 'configure', 'check', 'preview-sync', 'sync', 'build', 'publish'))
    args = parser.parse_args(argv)
    publisher = Publisher(args.repo, emit=lambda event: print(event['message']))
    try:
        config = publisher.load_config()
        if args.action == 'configure':
            if not config['releaseTag']:
                config['releaseTag'] = publisher.inspect_source()['releaseTag']
            for key, label in [('baseUrl', 'GitLab HTTPS 주소'), ('projectId', '프로젝트 숫자 ID'),
                               ('remoteName', '사내 원격 이름(origin 사용 안 함)'),
                               ('remoteUrl', '사내 저장소 SSH 주소(소스 반영을 생략하면 비워 둠)'),
                               ('releaseTag', '함께 반영할 릴리스 태그'), ('tokenKind', '토큰 종류 deploy 또는 job'),
                               ('title', '변경 안내 제목'), ('notes', '변경 안내')]:
                entered = input(f"{label} [{config[key]}]: ").strip()
                if entered:
                    config[key] = entered
            origins = input('추가 다운로드 허용 HTTPS 출처(쉼표로 구분, 없으면 Enter): ').strip()
            if origins:
                config['allowedDownloadOrigins'] = [value.strip() for value in origins.split(',') if value.strip()]
            result = publisher.save_config(config)
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
            result = publisher.load_last_build(config)
            if result is None:
                raise PublisherError('먼저 build 명령으로 배포 파일을 만들어 주세요.')
            token = getpass.getpass('게시용 토큰(저장하지 않음): ')
            try:
                result = publisher.publish(config, result, token, token_kind=config['tokenKind'])
            finally:
                token = ''
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except PublisherError as exc:
        print(str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
