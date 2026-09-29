# atria-keys

`atria-keys` is a self-contained, local provisioning harness for provider-approved Atria
service accounts. It checkpoints each stage, persists keys with an fsynced append-only
JSONL store, rate-limits all runs sharing the machine's egress IP, and exposes a
localhost-only read-only operations dashboard.

The public Atria console currently advertises Google/OIDC sign-in. If your partnership
uses a separate email registration page, set `registration_url` in `config/atria.yaml`
to the provider-issued URL. Auth-flow or widget drift is treated as fatal and produces a
DOM artifact; the harness never guesses its way through a changed page.

## Safety boundary for the challenge

The live driver keeps the provider widget intact and waits for its genuine completion
signal after an operator completes it in the visible local browser. It does not forge a
challenge token, suppress canvas/fingerprint code, or synthesize human-like gestures.
This keeps the integration within the widget's intended contract. The offline fixture
driver is fully automatic and is used by the test suite.

## Layout

```text
atria_keys/
  pipeline.py    stage runner and resume logic
  mailbox.py     MailboxReader protocol + catch-all IMAP implementation
  driver.py      Playwright registration/session driver
  challenge.py   ChallengeDriver protocol + safe Alibaba widget observer
  keystore.py    locked, fsynced JSONL store and exports
  dashboard.py   localhost read-only status page
  pacing.py      single-lane interval/day-limit/backoff enforcement
config/atria.yaml
tests/
keys/            runtime data (gitignored)
```

## Setup

Python 3.11 or newer is required.

```bash
cd atria-keys
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
playwright install chromium
```

Edit `config/atria.yaml`, point `registration_url` at the provider-issued registration
page, and set the configured IMAP password environment variable. Runtime files are
created with restrictive permissions under `keys/`.

## Use

Run the deterministic no-network fixture flow:

```bash
python -m atria_keys.cli run --count 3 --fixture
```

Run the live flow (the visible browser pauses for local challenge completion):

```bash
python -m atria_keys.cli run --count 1
```

Runs left in `pending` or `running` state are picked up before new runs. A specific run,
including a failed run after its cause is fixed, can be resumed with:

```bash
python -m atria_keys.cli run --resume RUN_ID
```

Serve the dashboard separately at `http://127.0.0.1:8686`:

```bash
python -m atria_keys.cli dashboard
```

Export the durable store:

```bash
python -m atria_keys.cli export --format txt --output keys/keys.txt
python -m atria_keys.cli export --format env --output keys/keys.env
```

Key material is never written to logs or dashboard responses. `keys.jsonl`, exports,
session state, pending material, SQLite state, and artifacts all remain in the ignored
`keys/` directory.

## Tests

```bash
python tests/run_all.py
```

The suite covers all mocked stages, concurrent append integrity, deduplication,
checkpoint resume, crash-after-append recovery, pacing and challenge backoff,
unsupported-widget artifacts/fatal classification, recipient matching, and dashboard
redaction.
