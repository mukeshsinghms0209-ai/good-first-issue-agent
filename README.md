# Local Gemma Good-First-Issue Agent

A lightweight Python CLI that fetches open GitHub issues labeled `good first issue` and asks a local Gemma model, through Ollama, for a human-reviewable analysis and fix proposal. GitHub provides issue metadata and a bounded set of repository source files; model inference runs on your machine, with no hosted AI API.

## Project structure

```text
open-source-agent/
|-- agent.py
|-- README.md
`-- skills/
    |-- issue-reader.skill.md
    `-- code-fixer.skill.md
```

## Requirements and setup

- Python 3.10 or newer
- [Ollama](https://ollama.com/) installed and running
- A small Gemma model pulled locally; the default is `gemma3:1b`
- Internet access to retrieve public GitHub issue metadata

```powershell
ollama pull gemma3:1b
ollama serve
```

In another terminal, from this project directory:

```powershell
python agent.py --repo pallets/flask --limit 1
```

By default, the agent fetches a bounded set of issue-relevant source paths from
the GitHub API and downloads their contents from GitHub's raw-content host. For
private repositories, API rate
limits, or offline source access, clone the repository and pass its checkout:

```powershell
git clone https://github.com/pallets/flask.git .\flask
python agent.py --repo pallets/flask --repo-dir .\flask --limit 1
```

To analyze more matching issues:

```powershell
python agent.py --repo pallets/flask --limit 3
```

If GitHub's unauthenticated API rate limit is reached, set an optional token in
your shell. The token is used only for GitHub API authentication and is never
sent to Ollama:

```powershell
$env:GITHUB_TOKEN = "YOUR_GITHUB_TOKEN"
python agent.py --repo pallets/flask
```

Set `OLLAMA_MODEL` or `OLLAMA_URL` to override the defaults, or pass `--model`
and `--ollama-url`. The model name must already be available in Ollama.
For privacy, the agent only accepts Ollama URLs on `localhost` or a loopback IP;
remote model endpoints are rejected.

Run the dependency-free test suite with:

```powershell
python -m unittest discover -s tests -v
```

## How it works

1. Loads and validates both Markdown skills from `skills/`.
2. Fetches open issues with the conventional `good first issue` label from the
   specified public repository, excluding pull requests.
3. Fetches up to 12 relevant source files from GitHub, or reads them from
   `--repo-dir` when provided; common dependency/build directories and
   non-source files are excluded.
4. Runs the issue-reader skill as a first local model request, then sends its
   analysis, source context, and fixer skill in a separate request to Ollama's
   local `http://localhost:11434/api/generate` endpoint.
5. Prints the model's analysis and proposed changes for a developer to review.

Issue text and source files are treated as untrusted input. Source files are
sent only to the loopback Ollama endpoint; remote Ollama endpoints are rejected.
With `--repo-dir`, the tool reads but never modifies the checkout. It does not
execute generated code, create branches, or submit pull requests. Review the
proposal and run the target repository's tests before using any generated patch.

## Hackathon pitch

> Open source has thousands of beginner-friendly issues, but turning an issue
> into a safe, understandable first contribution is still intimidating. This
> project uses a local Gemma model through Ollama to turn GitHub's good-first-
> issue queue into a practical, reviewable fix proposal. Its skills separate
> understanding the issue from drafting a solution, while keeping inference
> on-device and the developer in control. No cloud AI, no automatic edits, and
> no blind pull requests—just a faster, privacy-conscious starting point for
> the next open-source contributor.

## Live demo guide

1. **Show the local model:** run `ollama list` and point out `gemma3:1b`.
2. **Choose a real issue:** clone a repository with open `good first issue`
   labels, then run `python agent.py --repo OWNER/REPO --repo-dir .\checkout`.
3. **Explain the architecture:** show the two files in `skills/`. The reader
   first extracts request, criteria, assumptions, and risks; the fixer then
   uses that analysis and source context to draft a proposal.
4. **Review the output:** compare the issue link, acceptance criteria, and
   proposed patch or outline with the actual repository. Explain that the
   proposal is not applied automatically and should be tested by a maintainer.
5. **Highlight the tradeoff:** issue and source discovery needs GitHub network
   access, while inference is local; prompts are not sent to hosted model
   services.

If Ollama is unavailable, the CLI reports a connection error with recovery
guidance. Use `python agent.py --help` to see all options.
