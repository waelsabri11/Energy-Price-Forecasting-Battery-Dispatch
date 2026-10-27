# Azure cost estimate for `infra/` (before any `terraform apply`)

Date: 2026-10-27. Region `francecentral`. Unit prices are the **public retail list prices in USD** from the Azure Retail Prices API (queried today, no tax); quantities are my assumptions, not measurements. Subscription: "Azure subscription 1" (free trial, $200 credit for 30 days, per Wael).

## Per resource, running 24/7 (730 h / 2,628,000 s per month)

| Resource | Config in `infra/main.tf` | Unit price (API) | ~USD / month |
|---|---|---|---|
| PostgreSQL Flexible Server | B1ms, 32 GB, no HA | $0.019/h compute + $0.133/GB-month storage | 13.9 + 4.3 = **18.1** |
| Container Registry | Basic | $0.1666/day (10 GB included) | **5.0** |
| MLflow Container App | 1 replica always on, 0.5 vCPU / 1 GiB | idle $0.000003 per vCPU-s and per GiB-s; monthly free grant 180k vCPU-s / 360k GiB-s (my recollection of the pricing page, not in the API) | **~10** |
| Daily job | 1 vCPU / 2 GiB, ~2 min/day (113 s measured on kind) | ~3.6k vCPU-s + 7k GiB-s per month | **~0** (inside the free grant) |
| API Container App | scale to zero, 0.25 vCPU / 0.5 GiB | billed only while serving | **~0** |
| Container Apps environment | Consumption plan | no fixed charge for the consumption environment | **0** |
| Storage account (2 file shares, LRS) | < 1 GB used, 25 GB quotas | $0.0288/GB-month hot | **< 0.1** |
| Key Vault (standard) | 1 secret | $0.03 per 10k operations | **~0** |
| Log Analytics | PerGB2018 | first 5 GB/month free (recollection); our logs are tiny | **~0** |
| **Total** | | | **~33 / month**, ~32 over the 30-day trial |

Not measured: real log volume, share transaction costs, whether the Postgres free-account offer applies (see below). Prices are list prices; the trial credit consumes at list price as far as I know.

## What keeps costing after the 30 days

The trial credit expires after 30 days. If the subscription is not upgraded to pay-as-you-go, Azure disables it and the resources stop (and can be deleted later); no card charge happens without an upgrade. If Wael upgrades, the always-on part is **~$33/month** as-is (Postgres + ACR + MLflow); the free-account 12-month offers (B1ms Postgres 750 h + 32 GB, by my recollection of the free-account page; to be checked in the portal) could cancel the $18 Postgres line for the first 12 months only.

## Variants

* **A. As-is (recommended)**: no code change, already `terraform validate`d. Deploy, run the job, verify, record evidence, then `terraform destroy` within ~2 days. Credit used: about **$2** (2 days × ~$1.1/day). Real money: $0.
* **B. Cheapest persistent**: drop Postgres (MLflow on SQLite on the Azure Files share, MLflow scaled to zero). About **$5/month** (ACR only). Needs a `use_postgres` switch in Terraform; SQLite on SMB shares can hit locking errors, so it is a demo-grade compromise.
* **C. No MLflow on Azure**: the job loads `model.joblib` from the file share (`energy infer` already accepts a path). About $5/month, but loses the registry part of the story.
* **AKS (documentation only, not created)**: control plane free tier, but at least one VM node; a single `Standard_B2s` is about $0.0416/h ≈ **$30/month** (my recollection of the price, not queried) plus disks and load balancer, so ~$35-45/month on top of nothing else. The Container Apps setup already runs on Kubernetes underneath; kind manifests in `k8s/` are portable to AKS.

## Risks to know before applying

* The four resource providers (`Microsoft.App`, `ContainerRegistry`, `DBforPostgreSQL`, `OperationalInsights`) are **NotRegistered** on the subscription today; registration is free and I would run it.
* Trial subscriptions sometimes restrict Postgres Flexible Server in some regions (`francecentral` could refuse); if so I would try another region and tell Wael.
* `terraform` is not installed (no Homebrew): I would download the pinned HashiCorp 1.9.x binary and verify its checksum.
* The deployed MLflow has no authentication and stays internal-only; the API is public by design (read-only forecast).
* Needs from Wael at apply time: the ENTSO-E key (read from `.env`, passed as a Terraform variable, ends in Terraform state locally; the state file is git-ignored) and a generated Postgres admin password.

## Decision (2026-10-27): variant A + 1-node AKS, 2 days, then destroy

* AKS node: one `Standard_B2ms` (2 vCPU / 8 GiB, $0.0944/h queried), free control-plane tier, no autoscaler, 64 GB OS disk. Optional in Terraform (`enable_aks`, default false).
* Cost guards: free-trial subscription with `spendingLimit: On` (read from the subscription via the API), a $10 budget `energy-demo-10usd` with e-mail alerts at 50/80/100 % (alerts only, they do not stop spending), Log Analytics daily ingestion cap of 1 GB, MLflow and API limited to 1 replica.
* Expected credit use over the 2 days: about $9.5 (A ~$2 + node $4.5 + public IP, load balancer and disk ~$3, the last two from memory and not queried).
* `terraform plan` (AKS enabled): 24 to add, 0 to change, 0 to destroy.
