# Code-Signing Policy

## Current alpha status

Bazaar Coach alpha builds are currently unsigned unless the specific GitHub Release asset shows a valid Windows digital signature. Unsigned alpha builds may trigger Windows SmartScreen or antivirus warnings — see [packaging/installer/README.md](packaging/installer/README.md) for what to expect and how to add exclusions.

## Planned signing provider

Windows releases are planned to be signed via **[SignPath.io](https://signpath.io)** using a certificate issued by the **SignPath Foundation** under its Open-Source Software code-signing program, once the onboarding and application approval process is complete.

<!-- TODO: SignPath attribution per onboarding -->
<!-- Insert the exact attribution text, badge, or logo supplied by SignPath during
     the onboarding process here. Do not publish this section until onboarding is
     complete and the attribution requirements are confirmed. -->

The CI build pipeline (`.github/workflows/release-build.yml`) has a SignPath signing slot reserved between the installer build and artifact upload steps (see the `# --- SignPath slot-in (issue #154) ---` comment). It will be enabled once the SignPath Foundation application is approved.

## Team and roles (planned)

Signing requests will follow the SignPath model of Author / Reviewer / Approver roles:

| Person | Role(s) |
|--------|---------|
| Matthew Hearn ([@hearn1](https://github.com/hearn1)) | Author, Reviewer, Approver |

Signing requests will require **manual per-release approval**. No signing will be triggered automatically without an explicit approval action from an Approver.

## Verify checksums (all releases)

Each GitHub Release at <https://github.com/hearn1/bazaar_coach/releases> publishes SHA-256 checksums for the installer and portable zip. Compare the checksum of the file you downloaded against the published value before running it:

```powershell
(Get-FileHash BazaarCoachSetup-<version>.exe -Algorithm SHA256).Hash
```

## How to verify a signature (signed releases only)

Use this section only for release assets that carry a valid Windows digital signature.

### Windows file Properties

1. Right-click the installer (`BazaarCoachSetup-<version>.exe`) in Explorer.
2. Choose **Properties** → **Digital Signatures** tab.
3. Select the signature entry and click **Details** to confirm the signer name and certificate chain.

### signtool (command line)

```powershell
signtool verify /pa /v BazaarCoachSetup-<version>.exe
```

A valid signed build outputs `Successfully verified: BazaarCoachSetup-<version>.exe`. An unsigned alpha build returns a verification error — that is expected until signing is live.
