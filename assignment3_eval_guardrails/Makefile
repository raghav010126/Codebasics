# MediGuard: evaluation & guardrail pipeline around MediBot. Heavy files (venv, index, caches, runs, logs) live
# under ~/.cache/mediguard so nothing large lands in this (OneDrive-synced) folder.
export UV_PROJECT_ENVIRONMENT := $(HOME)/.cache/mediguard/venv
export PYTHONPYCACHEPREFIX := $(HOME)/.cache/mediguard/pycache
RUN := uv run --no-sync
ROLE ?= doctor
Q ?= What is the standard dose of Vancomycin and what trough level is targeted?
SWAP_MODEL ?= claude-haiku-4-5

.PHONY: setup build-index guardrail-setup doctor ask ui redteam eval eval-twice baseline swap-demo report logs \
        test target-test lint demo-offline

setup:            ## install dependencies (Python 3.12, uv)
	uv sync --python 3.12

build-index:      ## build MediBot's Qdrant index (SEED=~/Downloads/medibot/.cache reuses parsed PDFs)
	$(RUN) mediguard target build-index $(if $(SEED),--seed-from $(SEED))

guardrail-setup:  ## create/update the Bedrock guardrail; copy the printed id/version into .env
	$(RUN) mediguard guardrail-setup

doctor:           ## check keys, AWS, guardrail, index, pristine MediBot copy
	$(RUN) mediguard doctor

ask:              ## make ask ROLE=nurse Q="..." [EXPLAIN=1 for the operator view] [ARGS="--no-bedrock"]
	$(RUN) mediguard ask --role $(ROLE) "$(Q)" $(if $(EXPLAIN),--explain) $(ARGS)

ui:               ## chat UI (Streamlit) through the guarded pipeline; stop it before eval/redteam (one Qdrant process)
	$(RUN) streamlit run src/mediguard/ui.py $(ARGS)

redteam:          ## adversarial suite -> reports/guardrail_redteam.md (ARGS="--no-bedrock" without AWS)
	$(RUN) mediguard redteam $(if $(findstring --no-bedrock,$(ARGS)),,--break-bedrock) $(ARGS)

eval:             ## full evaluation -> PASS/FAIL report (ARGS="--fresh-target --publish latest")
	$(RUN) mediguard eval $(ARGS)

eval-twice:       ## repeatability: two runs on an unchanged system, then compare
	-$(RUN) mediguard eval --label repeatA $(ARGS)
	-$(RUN) mediguard eval --label repeatB $(ARGS)
	$(RUN) mediguard compare latest:repeatA latest:repeatB

baseline:         ## save the latest run (or RUN_ID=<id>) as baselines/baseline.json
	$(RUN) mediguard baseline $(RUN_ID)

swap-demo:        ## incident #3: silently swap MediBot's answer model, compare with the baseline
	-$(RUN) mediguard eval --model $(SWAP_MODEL) --baseline baselines/baseline.json --label swap-$(SWAP_MODEL) $(ARGS)

report:           ## rebuild the latest report (RUN_ID=<id>)
	$(RUN) mediguard report $(RUN_ID)

logs:             ## make logs ARGS="--request req_..." | "--blocks" | "--latency" | "--tokens"
	$(RUN) mediguard logs $(ARGS)

test:             ## offline tests (needs the built index)
	$(RUN) pytest -q -p no:cacheprovider

target-test:      ## MediBot's own test suite, run against the unmodified copy
	cd target/medibot && $(RUN) pytest -q -p no:cacheprovider -m "not slow" tests/test_rbac.py tests/test_router.py

demo-offline:     ## guardrail proofs without any API: fail-closed matrix, controls, output leaks via stubs
	$(RUN) pytest -q -p no:cacheprovider tests/test_heuristics_and_failclosed.py tests/test_pipeline_and_replay.py tests/test_verdict.py

lint:             ## ruff + a secret scan of tracked files
	$(RUN) ruff check src tests scripts
	@! git grep -nE "sk-ant-[A-Za-z0-9_-]{20,}|lsv2_(pt|sk)_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}" -- . ':!*.lock' || \
		(echo "secret-like string in tracked files" && false)
