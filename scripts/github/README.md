# GitHub Utilities

Inspect Actions runs and create pull requests with the existing GitHub CLI helpers.

## gh-utils.sh - Unified GitHub Operations

### Usage

```bash
./scripts/github/gh-utils.sh <command> [args]
```

### Commands

| Command        | Description                              |
|----------------|------------------------------------------|
| `logs <run-id>`| Fetch CI logs for a specific run         |
| `watch`        | List the latest run for this branch, then open `gh run watch` |
| `pr <title>`   | Create a PR with a generic body, then open the watcher |
| `help`         | Show help message                        |

### Examples

```bash
# Watch CI for current branch
./scripts/github/gh-utils.sh watch

# Fetch logs for run ID 1234567890
./scripts/github/gh-utils.sh logs 1234567890

# Get run ID from current branch
gh run list --branch $(git branch --show-current)

# Create PR and automatically watch CI
./scripts/github/gh-utils.sh pr "Fix authentication bug"
```

### Prerequisites

- GitHub CLI (`gh`) installed and authenticated
- Repository with GitHub Actions enabled

The helper does not bind the watcher to the listed run ID. For an exact result, select the intended run or use `gh run watch <run-id>`. After creating a PR, replace the generic body with the problem, change and validation evidence.

### Common Workflows

#### Create PR and Monitor CI

```bash
# One-liner to create PR and watch
./scripts/github/gh-utils.sh pr "Add new feature"
```

#### Debug Failed CI Run

```bash
# List recent runs
gh run list --branch $(git branch --show-current)

# Fetch detailed logs
./scripts/github/gh-utils.sh logs <run-id>
```

#### Monitor CI Without Creating PR

```bash
# Watch the latest CI run for current branch
./scripts/github/gh-utils.sh watch
```

### GitHub CLI Tips

```bash
# Check authentication status
gh auth status

# View PR list
gh pr list

# View workflow runs
gh run list

# Download artifacts from a run
gh run download <run-id>
```
