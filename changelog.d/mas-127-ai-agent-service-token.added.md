- **An AI agent gets an API token of its own.** The settings pane's API
  Access Tokens offer an *AI Agent* token beside the Jupyter Notebooks one,
  to anyone signed in (guest or above), for their own account. An AI agent
  working through the SDK presents it as the `ai-agent` service
  (`MascopeClient(service_name="ai-agent")`): it opens the same routes a
  notebook's token opens, with the same person's permissions, and nothing
  more, but under a name of its own; that includes changing and deleting what
  the person's role allows. The backend's access log now records the service
  an API token was minted for on every request that presents it under that
  name (`service_name`, empty for the web app's session), so an agent's requests
  are told apart from the person's own. It does not pair; generating it leaves
  the notebook token working.
