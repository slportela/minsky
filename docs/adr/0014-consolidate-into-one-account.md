# 0014. One account: the lake and bronze move to the second account; Bedrock follows when a model is invocable

- Status: accepted for the lake and bronze (rebuilt 2026-10-04); the Bedrock part is not usable yet
- Date: 2026-10-04
- Supersedes: the data placement of 0012 (its compute decision stands)

## Context

ADR 0012 split the work across two accounts of the same owner: compute in the second
account opened 2026-10-04, while "the S3 lake, bronze, the organizer source and Bedrock
stay where they are. Nothing is copied between accounts." That split costs two IAM users,
two sets of keys and two CLI profiles, and every `make` target that touches data has to be
run with the right one. The owner decided to operate a single profile, `personal`, against
the second account.

Nothing about the primary account recommends keeping the data there. Its binding defect is
the one that forced 0012: applied compute quotas of 0 (EC2 standard vCPU, Lightsail, Fargate),
which AWS has to lift and the owner cannot restore alone. That is unchanged by this ADR.

Two findings make consolidation cheap:

- **The lake is reproducible from the organizer source, not only from itself.**
  `pipeline/ingest_bronze.py` lists the organizer bucket with the organizer's read-only keys
  from `.env` and writes to `$BRONZE_URI` using `$BRONZE_AWS_PROFILE`. Those organizer
  credentials belong to neither of our accounts, so rebuilding bronze in a new bucket needs
  no cross-account copy, no bucket policy and no access to the old account.
- **Nothing hardcodes the bucket.** `BRONZE_URI` in `.env` is the only input; `LAKE_URI` is
  derived from it (`Makefile:5-7`). Silver and gold are rebuilt locally by dbt and uploaded
  by `make publish`, so they do not need to be moved either.

Bedrock is not on the serving path today: the interim provider of ADR 0008 is, and issue #5
(enabling Claude models in Bedrock) is still open. So moving Bedrock between accounts moves a
capability the system does not currently call.

## Decision

One AWS account (the second one) and one CLI profile, `personal`.

- **A new bucket in the second account, `us-east-2`, with versioning enabled**
  (`ingest_bronze.py` relies on versioning to keep the old bytes when a source file returns
  with a new ETag).
- **`us-east-2` for the bucket and for Bedrock**, matching the profile's own default region
  and the VM. Nothing in the repository pins the *bucket's* region: it is read only through
  `BRONZE_URI`, and `boto3.Session(profile_name=...)` in `ingest_bronze.py` takes the region
  from the profile. The organizer's region does not pair with ours either, because the ingest
  passes through the workstation rather than copying server-side. The Lightsail instance stays
  in `us-east-2` and its CDN in `us-east-1`, as deployed; the backend's `aws_region` default
  (`config.py:17`) still reads `us-east-1` and is overridden by configuration.
- **Bronze is rebuilt from the organizer source, not copied.** Measure first with
  `ingest_bronze.py --dry-run`, which reports the volume and a per-table breakdown.
- **`.env` changes three lines**: `BRONZE_URI`, `BRONZE_AWS_PROFILE` and `AWS_PROFILE`.
  `AWS_REGION` is **not** touched: it is the organizer bucket's region, consumed only by
  `pipeline/bank_data.py:21` (`.env.example:3-5`), not ours.
- The organizer keys stay in `.env` and out of `~/.aws`, and the VM still never receives
  `.env`, organizer credentials or an AWS credential directory (ADR 0011, ADR 0012).
- **Bedrock moves with the account only once a model is invocable.** See the Outcome: none is yet.
- **The primary account's bucket is not deleted** until a full pipeline run against the new
  bucket is verified. Until then the fallback is to point `BRONZE_URI` back.

## Consequences

- One profile, one set of keys, one bill. Data and hosting sit in one account owned by the
  team. Rule 6 of `AGENTS.md` is **not** fully restored: the models still go to the provider
  directly under ADR 0008, and Bedrock is not usable yet.
- The ingest passes through the workstation, so the whole volume crosses its connection twice.
  That bounded how much could be moved, and is why `digital_events` was left out (see Outcome).
- The lake now lives under the Free plan's expiry: the account closes when the USD 180
  credits run out or after 2027-04-04, and the plan excludes some services. That is after
  2026-10-16, so it holds for the hackathon, but it is not a production arrangement.
- Moving the data the day before the submission was a real risk. It was accepted because the
  old bucket survives the move and the rollback is one `.env` line.
- Two accounts still exist. This ADR consolidates the *working* account; the primary one
  keeps its quota case open with AWS and can be reclaimed later.
- Production is unchanged: `docs/architecture.md` keeps the single-account design.
- How we would know it was wrong: the volume does not fit the time left; the Free-plan policy
  denies S3 or a real Bedrock invocation that the control plane said was authorized; or silver
  and gold built from the new bucket do not reproduce the recorded counts. The first two did not
  happen for S3, the third did not happen (4,425,008 transactions), and the second **did happen
  for Bedrock** (see below).

## Outcome (2026-10-04)

**The lake was rebuilt in the second account.**

- Bucket `minsky-lake-116307286810`, `us-east-2`, versioning on, all four public-access blocks on.
- **Bronze holds 12 of the 13 source tables**: 6,574 of the 7,671 source files and 1,592.0 of
  5,349.3 MB. `digital_events` (1,097 files, 3,757.4 MB) was not ingested, because silver does
  not use it yet (`SKIP = {"digital_events"}` in `pipeline/transform/generate_silver.py`).
  Bronze is therefore **not a complete copy** of what the source delivered.
- The ingest copied 6,574 files in 1,580 s with 0 failures, and the bucket holds 1,591,967,430
  bytes under `bronze/data/`, as the dry-run predicted, plus the run manifest. A first attempt was
  cut off by a 10-minute tool limit after 2,947 objects (502.6 MB). The manifest is written last,
  so the rerun copied everything again and those objects have a second version.
- Silver built its 12 models. Of the 234 dbt nodes (1 seed, 12 models, 221 tests), 228 passed, 5
  tests warned (the data issues already in `docs/known_issues.md`) and 1 test errored: the gold test
  `transactions_complete` is selected with the silver models but reads the `bank` schema, which only
  the gold step creates.
- Gold built its 6 read models, and all 31 nodes (6 models, 25 tests) passed. It loaded them into
  Postgres `bank.*`.
  `bank.transactions` has **4,425,008 rows**, the count ADR 0012 recorded, so the lake rebuilt from
  the source reproduces the earlier one.
- `make publish` uploaded silver (227 objects, 371,295,268 bytes) and gold (6 objects, 229,541,608
  bytes).
- The primary account was not accessed and nothing in it was changed or deleted.

**Bedrock is not blocked by the Free-plan policy, but no model is invocable yet.** Tested by
invocation in us-east-2 (all three calls failed, so none was billed):

- `us.anthropic.claude-haiku-4-5-20251001-v1:0` → `ResourceNotFoundException`: "Model use case
  details have not been submitted for this account." A one-time Anthropic use-case form in the
  Bedrock console, then about 15 minutes. This is the path to close issue #5.
- `anthropic.claude-haiku-4-5-20251001-v1:0` → `ValidationException`: on-demand throughput needs
  an inference profile id, i.e. the `us.` prefix. Expected, not a permission problem.
- `us.openai.gpt-6-luna` → `AccessDeniedException`: "not available for this account", even though
  `list-foundation-models` lists it and the `us.openai.gpt-6-luna` inference profile reports ACTIVE.
  Consistent with `agreementAvailability: NOT_AVAILABLE` from `get-foundation-model-availability`.

None of these is a service control policy denial, unlike Lightsail `CreateInstances`. **Listing a
model is not access**: `list-foundation-models` and `list-inference-profiles` both reported
availability that invocation refused. The interim provider therefore cannot be replaced by *the same
model* on Bedrock. Moving to Bedrock means moving to Claude, which is a model change and needs an
eval delta under rule 3, not a configuration swap. Whether the Responses API calls the client makes
(`responses.parse`) work on Bedrock's OpenAI-compatible endpoint has not been verified. ADR 0008
stays in force; it and issue #5 should be revisited once the use-case form clears, on evals.
