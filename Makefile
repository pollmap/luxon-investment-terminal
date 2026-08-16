.PHONY: dev api web test build verify

dev:
	powershell -ExecutionPolicy Bypass -File ./scripts/dev.ps1

api:
	python -m uvicorn services.api.main:app --reload --port 8000

web:
	pnpm dev

test:
	python -m pytest
	pnpm --filter @personal-fastgraphs/web test

build:
	pnpm build

verify:
	python -m pytest
	pnpm build
	pnpm --filter @personal-fastgraphs/web test

