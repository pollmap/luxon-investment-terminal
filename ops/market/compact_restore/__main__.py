"""Offline readiness output only. No operational run/apply option is exposed."""

import argparse

from .checkpoint import main


def cli():
    parser = argparse.ArgumentParser(description="압축 복원 구성요소의 오프라인 준비 상태")
    parser.parse_args()
    main()


if __name__ == "__main__":
    cli()
