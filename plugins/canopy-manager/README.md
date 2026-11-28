# Canopy manager

Skills for setting up and running a [Canopy](https://github.com/danielrosehill/Canopy) server.
Deploy it where the server runs. Its skills would cost context in every session, so on a client,
add it only while setting up, then remove it:

```
/plugin marketplace add danielrosehill/Canopy
/plugin install canopy-manager@canopy
```

Everyday use needs only [`canopy-connector`](../canopy-connector/README.md). A server indexes this
plugin, so its skills can still be found by search from any client.

Hosted clients: `debug-chatgpt` and `debug-claude-ai` find why ChatGPT or claude.ai cannot reach Canopy, or reach it
and miss a skill or tool. They share the server-side checks in `references/hosted-client-checks.md`.
