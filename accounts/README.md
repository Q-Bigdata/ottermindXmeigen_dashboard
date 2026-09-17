# MeiGen account analysis

The current deliverable is `summary.json` (identical to `account_analysis.json`). `account_facts.json` contains one salted hash per linked account; `anchors.json` describes the first qualifying MeiGen entry. Source data and all query evidence are in `extract/`. The `superseded_transport_probes/` folder is excluded from the analysis.

Recompute a single frozen generation:

```sh
python3 extract_accounts.py context --end <cutoff> --data-dir <generation/data> --output-dir <generation/accounts>
python3 extract_accounts.py identity --end <cutoff> --data-dir <generation/data> --output-dir <generation/accounts>
python3 extract_accounts.py history --end <cutoff> --data-dir <generation/data> --output-dir <generation/accounts>
python3 extract_accounts.py payments --end <cutoff> --data-dir <generation/data> --output-dir <generation/accounts>
python3 analytics_accounts.py --end <cutoff> --data-dir <generation/data> --output-dir <generation/accounts>
```

`CHAT2DB_MCP_TOKEN` is required only for extraction and is never written to analysis files. If `--end` is omitted, extraction freezes one `now - 120 seconds` cutoff for that invocation. The refresh pipeline should always provide one cutoff across all invocations. SQL only reads `umami.public`.

The main cohort follows the parent's first-pageview referrer-or-UTM definition. Identity requires one account throughout the anchor Visit, consistency with the current session record, and a mapping recorded before that Visit ends. A later identification in the same Visit permits retrospective attribution of the initial anonymous steps but does not imply entry-time login. Only later/prior history at or after a unique mapping's effective timestamp is assigned to an account. Context and mapping data are queried separately, without joining large source/identity CTEs.

Every account has the concrete demand of its first entry. The cumulative matrix uses mutually exclusive display states and also preserves registration, submission, reuse, payment-intent and payment flags. Registration is an observed `sign_up`/`guest_signup` signal; usage is `send_message`, a submission signal. V1 is an ordered registration signal then submission. Results and downloads require future instrumentation and are not inferred from these events.

The exposure window is `[t0, t0 + 24 hours)`. A 7-day outcome requires a new Visit starting in `(t0 + 24 hours, t0 + 8 days]`, with eligible events before that endpoint; 14-day outcomes end at `t0 + 15 days`. The cutoff must be strictly later than the complete endpoint. Actual Visit starts are looked up separately so a delayed identify event cannot create a false return. Counts from differently mature 7/14-day cohorts must not be compared as a trend in the same people.

Return means another observed web Visit. Reuse means another Visit with a submission. Another composer direction is explicit feature exploration, not successful adoption. Another landing page is reported separately. Referrer, UTM-only and authentication-return UTM cohorts are shown separately as a source sensitivity view. Strict post-link-only first-day submission bands are also available.

Proportions carry Wilson intervals. Comparisons standardized by entry demand and week only use strata with at least five accounts in each compared group, and report common-support size. They describe associations; they do not establish that first-day activity causes subsequent use.

Purchase events are grouped by hashed transaction ID. Missing transaction IDs remain separate events; conflicting transaction identity/value attributes are flagged. Trial checkout is not trial conversion. All-source account history and payment observations are followed after the first MeiGen entry.
