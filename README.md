# Dapr Windows mDNS repro

This test starts a local HTTP app and native Dapr sidecars on Windows. It calls the app through the sidecar's HTTP service invocation API and checks whether the default self-hosted mDNS resolver can find the app by ID.

## Run with the released runtime

Use Python 3 and a Windows build of [Dapr 1.18.0](https://github.com/dapr/dapr/releases/tag/v1.18.0). From this directory, run:

```powershell
python .\repro.py "$env:USERPROFILE\.dapr\bin\daprd.exe" --app-ids repro-single --restarts 3 --output .\official-run
```

Use a new output directory for each run. Omit `--app-ids repro-single` to start two apps and exercise both cross-invocation directions and both autoinvocations.

## Compare the zeroconf revisions

This requires PowerShell 7, Git, Python 3, and Go 1.26.4 for Windows amd64. The script downloads Dapr source and Go dependencies and builds two full sidecar binaries. Run:

```powershell
pwsh -NoProfile -File .\reproduce-wifi.ps1
```

The script runs control, patched, and control again. It builds from the same Dapr tag with the same process-local restriction to Wi-Fi in both variants. Pass `-Interface` if your Wi-Fi adapter has a different name. The script leaves the Windows network configuration and installed Dapr runtime alone.

Each phase checks the application's direct `/ping` response and the sidecar's health endpoint before invoking `/ping` through Dapr. `PASS` means the expected app answered HTTP 200. `MDNS_500` means HTTP 500 with `ERR_DIRECT_INVOKE` and `couldn't find service`. `OTHER` records any other response separately.

## Observed results

In three Wi-Fi-only cycles using two sidecars, the baseline with zeroconf `v1.0.0` returned the target HTTP 500 in 24 of 24 requests. The patched revision returned HTTP 200 in 24 of 24. Returning to the baseline produced the same HTTP 500 in 24 of 24. Both builds used `components-contrib v1.18.0` and `golang.org/x/net v0.55.0`. The first invocation of the single-sidecar test also reproduced the error.

The released runtime passed the first 352 requests without interface restriction. A later smoke test and the first control phase of a follow-up each reproduced the target 500 with one sidecar and all interfaces available. The patched build passed all four follow-up invocations, and the released runtime also passed when reintroduced after each patched phase. The restricted control passed in a separate later run as well. The A/B/A result verifies the patch during the observed Wi-Fi-only failure. The unrestricted follow-up remains inconclusive because the control did not fail again after the patched phase.

The dependency candidate is [grandcat/zeroconf#110](https://github.com/grandcat/zeroconf/pull/110), included in `v1.0.1-0.20230119201135-e4f60f8407b1`. [dapr/components-contrib#520](https://github.com/dapr/components-contrib/issues/520) documents the same error signature in an older Windows setup.

Generated sidecar logs and Go build metadata can include hostnames, network addresses, and local paths. Review and redact them before sharing.
