# the sandbox an agentic local model works in: Claude Code (the CLI, pointed at the agency gateway) with python for running tests. Nothing of the host except the job's workspace copy is mounted;
# the entrypoint firewalls the container to the gateway's single address before dropping root.
FROM node:24-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip git ca-certificates iptables util-linux libc-bin && rm -rf /var/lib/apt/lists/* \
 && ln -sf /usr/bin/python3 /usr/local/bin/python \
 && npm install -g @anthropic-ai/claude-code && npm cache clean --force
COPY agent_entry.sh /usr/local/bin/agent_entry.sh
RUN chmod 755 /usr/local/bin/agent_entry.sh && sed -i 's/\r$//' /usr/local/bin/agent_entry.sh
ENV DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 HOME=/tmp CLAUDE_CONFIG_DIR=/tmp/cfg
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/agent_entry.sh"]
