r"""AX Workspace 설치 및 업데이트 기록에서 중복을 제거한 사번을 JSON으로 저장합니다.

Windows에서 사용하기
  1. 처음 한 번 터미널에서 py -m pip install pymongo 를 실행하세요.
  2. 아래 데이터베이스와 컬렉션 이름을 운영 웹 서버의 집계 설정과 맞추세요.
  3. py export_registration_employee_ids.py 를 실행하고 MongoDB 접속 주소를 입력하세요.
     다른 경로에 저장하려면 --output "C:\작업\사번목록.json" 을 덧붙이세요.

접속 주소는 해당 컬렉션을 읽을 권한이 있는 MongoDB URI입니다.
같은 사번으로 여러 버전을 설치하거나 업데이트해도 결과에는 한 번만 포함됩니다.
"""

import argparse
import getpass
import json
import os
import sys
import tempfile
import warnings
from datetime import datetime
from pathlib import Path


# 접속 주소를 비워 두면 실행할 때 화면에 표시하지 않고 입력받습니다.
MONGODB_URI = ""
MONGODB_DATABASE = "pt_ai_square"  # 운영 웹 설정의 MONGODB_DATABASE
MONGODB_COLLECTION = "ax_workspace_version_users"  # PTAI_AX_REGISTRATION_COLLECTION


def export_employee_ids(collection, output: Path) -> int:
    if output.exists():
        raise FileExistsError(output)

    pipeline = [
        {"$match": {
            "event": {"$in": ["initial_install", "app_update"]},
            "employeeId": {"$ne": ""},
            "$expr": {"$eq": [{"$type": "$employeeId"}, "string"]},
        }},
        {"$group": {"_id": "$employeeId"}},
        {"$sort": {"_id": 1}},
    ]
    count = 0
    temp = tempfile.NamedTemporaryFile(dir=output.parent, mode="w", encoding="utf-8",
                                       newline="\n", delete=False)
    temp_path = Path(temp.name)
    try:
        with temp:
            with collection.aggregate(pipeline, collation={"locale": "simple"},
                                      allowDiskUse=True, batchSize=1000) as rows:
                temp.write("[")
                for row in rows:
                    temp.write(",\n  " if count else "\n  ")
                    json.dump(row["_id"], temp, ensure_ascii=False)
                    count += 1
                temp.write("\n]\n" if count else "]\n")
        # 두 프로세스가 같은 이름으로 동시에 추출해도 먼저 만든 파일을 보존합니다.
        if os.name == "nt":
            os.rename(temp_path, output)
        else:
            os.link(temp_path, output)
    finally:
        temp_path.unlink(missing_ok=True)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="설치 및 업데이트 기록의 고유 사번을 JSON 목록으로 저장합니다.")
    parser.add_argument("--output", type=Path, metavar="파일.json",
                        help="저장할 새 파일 경로 (기본값은 현재 폴더에 날짜와 시간이 붙은 파일)")
    args = parser.parse_args()
    output = args.output or Path(datetime.now().strftime("ax_registration_employee_ids_%Y%m%d_%H%M%S_%f.json"))

    try:
        from pymongo import MongoClient
        from pymongo.errors import (
            ConfigurationError, OperationFailure, PyMongoError, ServerSelectionTimeoutError,
        )
    except ImportError:
        print("먼저 터미널에서 py -m pip install pymongo 를 실행해 주세요.", file=sys.stderr)
        return 1

    try:
        uri = MONGODB_URI.strip()
        if not uri:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                uri = getpass.getpass("MongoDB 접속 주소 입력 (입력한 글자는 화면에 표시되지 않습니다): ").strip()
        if not uri:
            print("MongoDB 접속 주소를 입력해 주세요.", file=sys.stderr)
            return 1
        with MongoClient(uri, appname="AXWorkspaceRegistrationExport",
                         serverSelectionTimeoutMS=7000, connectTimeoutMS=7000) as client:
            count = export_employee_ids(client[MONGODB_DATABASE][MONGODB_COLLECTION], output)
    except (EOFError, getpass.GetPassWarning):
        message = "터미널에서 실행해 주소를 입력하거나 파일 상단의 MONGODB_URI를 설정해 주세요."
    except KeyboardInterrupt:
        print("\n조회가 취소되었습니다.", file=sys.stderr)
        return 130
    except (ConfigurationError, ValueError):
        message = "MongoDB 접속 주소와 데이터베이스 및 컬렉션 이름의 형식을 확인해 주세요."
    except ServerSelectionTimeoutError:
        message = "MongoDB에 연결할 수 없습니다. 사내망 연결, 서버 주소 및 TLS 설정을 확인해 주세요."
    except OperationFailure as exc:
        if exc.code in (13, 18):
            message = "인증 정보와 해당 집계 컬렉션의 읽기 권한을 확인해 주세요."
        else:
            message = "MongoDB가 조회 요청을 처리하지 못했습니다. 서버 상태와 읽기 권한을 확인해 주세요."
    except PyMongoError:
        message = "MongoDB 조회가 중단되었습니다. 서버 연결 상태를 확인해 주세요."
    except FileExistsError:
        message = "같은 이름의 파일이 있습니다. --output 으로 다른 파일 이름을 지정해 주세요."
    except OSError:
        message = "파일을 저장할 수 없습니다. 저장 폴더의 경로, 쓰기 권한 및 여유 공간을 확인해 주세요."
    else:
        print(f"고유 사번 {count:,}개를 저장했습니다.\n저장 위치: {output.resolve()}")
        if count == 0:
            print("설치 또는 업데이트 기록이 0건입니다. 데이터베이스와 컬렉션 이름을 확인해 주세요.")
        return 0

    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
