# Resource Inventory — Week 1 Baseline

| Resource Name | Type | Resource Group | Region | Purpose |
|---|---|---|---|---|
| meridian-rg-dev-eus2 | Resource Group | N/A | East US 2 | Container for all dev resources |
| meridian-mi-dev-eus2 | Managed Identity | meridian-rg-dev-eus2 | East US 2 | Secretless auth for all app components |
| meridian-law-dev-eus2 | Log Analytics Workspace | meridian-rg-dev-eus2 | East US 2 | Central log sink |
| meridian-appinsights-dev-eus2 | Application Insights | meridian-rg-dev-eus2 | East US 2 | Distributed trace collection |

**Last Verified:** 2026-04-30  
**Verified By:** Samuel  
**az CLI Version:** 2.84.0
