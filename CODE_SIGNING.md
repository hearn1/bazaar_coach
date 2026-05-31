# Code-Signing Policy

## Signing provider

Windows releases of Bazaar Coach are signed via **[SignPath.io](https://signpath.io)** using a certificate issued by the **SignPath Foundation** under its Open-Source Software code-signing program.

<!-- TODO: SignPath attribution per onboarding -->
<!-- Insert the exact attribution text, badge, or logo supplied by SignPath during
     the onboarding process here. Do not publish this section until onboarding is
     complete and the attribution requirements are confirmed. -->

The CI build pipeline (`.github/workflows/release-build.yml`) builds the installer and portable zip on `windows-latest`. A SignPath signing step is reserved in that workflow (see the `# --- SignPath slot-in (issue #154) ---` comment) and will be enabled once the SignPath Foundation application is approved.

## Team and roles

Signing requests follow the SignPath model of Author / Reviewer / Approver roles:

| Person | Role(s) |
|--------|---------|
| Matthew Hearn ([@hearn1](https://github.com/hearn1)) | Author, Reviewer, Approver |

Signing requests require **manual per-release approval**. No signing is triggered automatically without an explicit approval action from an Approver.

## How to verify a signature

### Windows file Properties

1. Right-click the installer (`BazaarCoachSetup-<version>.exe`) in Explorer.
2. Choose **Properties** → **Digital Signatures** tab.
3. Select the signature entry and click **Details** to confirm the signer name and certificate chain.

### signtool (command line)

```powershell
signtool verify /pa /v BazaarCoachSetup-<version>.exe
```

A valid signed build outputs `Successfully verified: BazaarCoachSetup-<version>.exe`. An unsigned alpha build returns a verification error — that is expected until signing is live.

### Cross-reference published checksums

Each GitHub Release at <https://github.com/hearn1/bazaar_coach/releases> publishes SHA-256 checksums for the installer and portable zip. Compare the checksum of the file you downloaded against the published value before running it:

```powershell
(Get-FileHash BazaarCoachSetup-<version>.exe -Algorithm SHA256).Hash
```
