# Microsoft Foundry Deployment

## Recommendation

This repository is currently a local batch-processing CLI, not a deployable
Foundry hosted agent. For an operator-submitted reconciliation request, the
recommended Foundry target is a **hosted agent using the Invocations protocol**:
it can accept a job request, run the reconciliation code, and return links to
the results. For an unattended monthly batch, use a scheduled job such as Azure
Container Apps Jobs and call Foundry for model inference; a hosted agent alone
does not schedule the work.

The existing reconciliation and report code can be reused, but the local
Foundry Local startup flow, local-folder assumptions, and CLI entry point need
to be replaced or adapted before deployment. See [Microsoft's hosted-agent
overview](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
and [source-code deployment guide](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-code).

## Requirements

### Azure and development access

- An Azure subscription and a Microsoft Foundry project in a region that
  supports hosted agents and the selected model.
- The project must have an available model deployment compatible with the
  extraction calls. Confirm model availability, quota, and that the deployment
  supports the request format before implementation.
- An operator with the **Foundry Project Manager** role at the project scope
  for agent deployment.
- Azure CLI and Azure Developer CLI (`azd`) installed and authenticated to the
  intended tenant and subscription. Authenticate interactively before running
  the deployment steps; don't put credentials in the repository.
- Python 3.13 for the hosted-agent source-code deployment path, plus the
  `azure-ai-agentserver-invocations` protocol package and the project's
  runtime dependencies.

### Application work required before deployment

1. **Add an Invocations handler.** Create a hosted-agent entry point using the
   supported `azure-ai-agentserver-invocations` protocol. It should validate a
   request, start one reconciliation job, and return a clear completion status
   and references to its outputs. The current [main.py](./main.py) is a CLI and
   does not expose a hosted-agent protocol.
2. **Replace Foundry Local.** Remove the local server preflight, local
   `foundry` CLI dependency, and shutdown behavior. Update
   [recon/config.py](./recon/config.py) and [recon/extraction.py](./recon/extraction.py)
   to call the selected Foundry model deployment using Entra authentication
   supported by the hosted-agent runtime. Don't deploy the local Foundry
   endpoint or use a local API key in production.
3. **Move files to managed cloud storage.** Replace `--folder` and local
   filesystem inputs with approved Azure Blob Storage (or equivalent) locations
   for statements, client-trade workbooks, and master trackers. Write generated
   JSON, updated trackers, dashboards, and reports back to controlled storage.
   Grant the agent identity only the required permissions and scope them to the
   relevant containers.
4. **Choose an email authorization path.** For Microsoft Graph, use a managed
   identity/service principal with the necessary `Mail.Send` application
   permission and administrator consent, or use an approved mail-sending
   service. If a client secret is unavoidable, keep it in Key Vault and retrieve
   it securely at runtime; do not commit it or put it in `azure.yaml`.
5. **Harden data handling.** Brokerage statements and reports contain
   sensitive financial and personal information. Confirm organizational
   approval, data residency and retention requirements, private access controls,
   encryption, logging redaction, and recipient authorization before using real
   customer files. Don't deploy the sample data as production data.
6. **Resolve scanned-PDF behavior.** The current
   [recon/extraction.py](./recon/extraction.py) only reads a PDF text layer and
   raises an error when one is absent. Add and test an approved OCR path if
   scanned statements must be supported.
7. **Validate dependencies and runtime.** Confirm all dependencies in
   [requirements.txt](./requirements.txt) install in the supported Linux
   runtime and remove packages that are not needed. Test PyMuPDF and report
   generation in the target runtime, not only on a developer machine.
8. **Add operational controls.** Define request size limits, timeouts, retry
   behavior, duplicate-job protection, failure reporting, audit records, and
   retention/cleanup for uploaded and generated files. Require human review
   before treating model-extracted trades or flagged activity as authoritative.

## Deployment steps

These are the steps after the application changes above are implemented. They
deploy a hosted agent; they do not provision Azure resources from this guide.

1. **Select or create the Foundry project.** Decide whether to use an existing
   project or create a new one. Confirm its region, model deployment, quota,
   networking, and required access to storage and Microsoft Graph.
2. **Choose a Python Invocations sample.** With `azd` installed, inspect the
   available agent samples and select one that uses the Invocations protocol:

   ```bash
   azd ai agent sample list --language python --output json
   ```

3. **Scaffold the hosted-agent project.** Use the selected sample's manifest
   URL and onboard this repository as source. Set the entry point to the new
   hosted-agent handler, not the current CLI:

   ```bash
   azd ai agent init \
     -m "<selected-python-invocations-sample-manifest-url>" \
     --deploy-mode code \
     --runtime python_3_13 \
     --entry-point "<hosted-agent-entry-point>" \
     --src .
   ```

   Confirm the generated `azure.yaml` service uses `host: azure.ai.agent` and
   declares the Invocations protocol. For source-code deployment, configure
   `codeConfiguration`; use a container deployment only when system packages
   or other runtime requirements make source deployment unsuitable.
4. **Configure the azd environment.** Create or select a non-production azd
   environment. For an existing Foundry project, set the endpoint, project ID,
   model deployment name, tenant, and subscription using values supplied by the
   project owner. The exact model environment variable expected by the agent
   should match the selected sample. Keep secrets out of source control:

   ```bash
   azd env set AZURE_AI_PROJECT_ENDPOINT "<project-endpoint>"
   azd env set AZURE_AI_PROJECT_ID "<project-resource-id>"
   azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "<model-deployment-name>"
   azd env set AZURE_TENANT_ID "<tenant-id>"
   azd env set AZURE_SUBSCRIPTION_ID "<subscription-id>"
   azd env get-values
   ```

   If creating new Azure infrastructure, review the generated infrastructure
   and costs before provisioning. Provision only after the project and resource
   choices are approved:

   ```bash
   azd provision --no-prompt
   ```

5. **Run locally and test.** Configure non-production storage and mail access,
   then run the agent locally:

   ```bash
   azd ai agent run --no-client
   ```

   Follow the selected sample's local invocation instructions. Verify valid
   inputs, missing files, malformed workbooks, model timeouts, model output
   validation, OCR behavior (if implemented), report creation, email
   authorization, and cleanup.
6. **Deploy the agent.** From the directory containing `azure.yaml`, deploy the
   hosted-agent service:

   ```bash
   azd deploy --no-prompt
   ```

   A successful deployment creates an immutable agent version. A redeployment
   creates another version.
7. **Verify and smoke-test.** Confirm the agent is active and has an endpoint,
   then send one non-production request through the Invocations protocol:

   ```bash
   azd ai agent show --output json
   azd ai agent invoke "<test request>" --protocol invocations
   ```

   Check the returned status and output references, then verify the files in
   storage. Do not test by sending reports to real recipients.
8. **Prepare production operations.** Promote through a separate production
   environment after business and security review. Configure approved identity
   permissions, alerts, logging, retention, and rollback/version procedures.
   For a monthly schedule, configure a separate scheduler to submit the job and
   monitor its result.

## Current limitations to resolve

- The current pipeline expects local folders named `statements/`,
  `client_trades/`, and `master/`; hosted agents need cloud file access or
  another explicitly managed input mechanism.
- `main.py` starts and stops a local Foundry server. That lifecycle is not
  appropriate in a hosted runtime.
- The current extraction code does not perform OCR for scanned PDFs.
- The comparison pipeline catches per-PDF extraction failures and can continue
  with partial inputs. Production job responses should make partial completion
  explicit and should not present it as a complete reconciliation.

## References

- [Hosted agents in Microsoft Foundry](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
- [Deploy a hosted agent from source code](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-code)
- [Deploy a hosted agent](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent)
- [Install the Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
