# Multi-SWE-Bench Evaluation Pipeline

## Overview

Multi-SWE-Bench evaluates model-generated patches against software engineering benchmarks. The evaluation runs inside Docker containers with a specific workflow for applying patches and running tests.

---

## Evaluation Pipeline

### Phase 1: Image Building (`run_mode_image`)

1. **Base Image Creation**: Creates a base Docker image with the repository cloned and dependencies installed
2. **Instance Image Creation**: For each PR/instance:
   - Copies `prepare.sh`, `fix.patch`, `test.patch`, `fix-run.sh`, `test-run.sh` into the image
   - Runs `prepare.sh` which:
     - Checks out the base commit (`git checkout {pr.base.sha}`)
     - **Applies `metamorphic_base_patch` if present** (commits the transformation)
     - Runs initial build to warm cache (`mvn clean test` or equivalent)

### Phase 2: Instance Running (`run_mode_instance_only`)

For each instance:

1. **Write prediction patch to disk**:
   ```python
   # run_evaluation.py:698-700
   fix_patch_path = instance_dir.absolute() / "fix.patch"
   with open(fix_patch_path, "w") as f:
       f.write(self.patches[instance.pr.id].fix_patch)  # From --patch_files argument
   ```

2. **Run container with volume mount**:
   ```python
   # run_evaluation.py:720-725
   volumes={
       fix_patch_path: {
           "bind": instance.dependency().fix_patch_path(),  # "/home/fix.patch"
           "mode": "rw",
       }
   }
   ```
   - **The volume mount OVERWRITES the image's built-in `/home/fix.patch`** with the model's prediction

3. **Execute `fix-run.sh`** inside container:
   ```bash
   cd /home/{repo}
   git apply --whitespace=nowarn /home/test.patch /home/fix.patch
   mvn clean test ...
   ```

### Phase 3: Report Generation (`run_evaluation`)

- Parses test output logs (`fix-patch-run.log`)
- Compares results against expected test outcomes from dataset
- Generates final report with pass/fail statistics

---

## Key Files and Their Roles

| File | Location | Purpose |
|------|----------|---------|
| `fix.patch` (dataset) | Baked into image at `/home/fix.patch` | Golden fix patch (used only for cache warming in prepare.sh) |
| `fix.patch` (prediction) | Volume-mounted to `/home/fix.patch` | Model's prediction - **overwrites** the built-in one |
| `test.patch` | Baked into image at `/home/test.patch` | Test additions that expose the bug |
| `prepare.sh` | Baked into image at `/home/prepare.sh` | Setup script run during image build |
| `fix-run.sh` | Baked into image at `/home/fix-run.sh` | Script that applies patches and runs tests |
| `metamorphic_base.patch` | Baked into image at `/home/metamorphic_base.patch` | Metamorphic transformation patch (only present if dataset has `metamorphic_base_patch`) - kept for reference and re-application |

---

## Metamorphic Testing Integration

### What Metamorphic Transformations Do

Metamorphic transformations modify the codebase semantically while preserving behavior:
- Rename classes/methods (e.g., `SubTypeValidator` → `SubtypeValidator`)
- Rename files
- Refactor code structure

### How Metamorphic Patches Are Applied

The `Metamorphic` class in `multi_swe_bench/harness/metamorphic.py` provides:

```python
METAMORPHIC_PATCH_PATH = "/home/metamorphic_base.patch"

@staticmethod
def apply_metamorphic_patch_cmd(pr: PullRequest, commit_message: str) -> str:
    patch = pr.base.metamorphic_base_patch
    if patch is not None and patch != "":
        return (
            f"cat > {METAMORPHIC_PATCH_PATH} << 'EOF_METAMORPHIC_PATCH'\n"
            f"{patch}\n"
            f"EOF_METAMORPHIC_PATCH\n"
            f"git apply {METAMORPHIC_PATCH_PATH}\n"
            # Note: patch file is NOT deleted - kept for reference and re-application
            f"git add -A && git commit -m '{commit_message}'\n"
        )
    return ""
```

This is called in `prepare.sh` of each instance (e.g., `jackson_databind.py:223`):
```bash
git checkout {pr.base.sha}

# apply metamorphic patch (if present)
{Metamorphic.apply_metamorphic_patch_cmd(pr=self.pr)}

bash /home/check_git_changes.sh
```

**Important**: The patch file is saved to `/home/metamorphic_base.patch` and kept in the image for:
1. Reference/debugging purposes
2. Re-application after `git reset --hard` in MSWE-agent's `SWEEnv.reset()`

### Dataset Structure for Metamorphic Instances

```json
{
  "org": "fasterxml",
  "repo": "jackson-databind",
  "number": 1923,
  "base": {
    "label": "...",
    "ref": "...",
    "sha": "abc123",
    "metamorphic_base_patch": "diff --git a/... (renames SubTypeValidator to SubtypeValidator)"
  },
  "fix_patch": "diff --git a/...SubtypeValidator.java... (references TRANSFORMED filename)",
  "test_patch": "diff --git a/... (references TRANSFORMED filenames)"
}
```

---

## Critical Discovery: Metamorphic Evaluation Failure Mode

### The Problem

When evaluating metamorphic instances, **all patches fail with 0% pass rate** if there's a mismatch between:
1. The codebase state (after metamorphic transformation)
2. The model's prediction (which may target original filenames)

### Root Cause

**Workflow mismatch:**

```
Expected Flow:
[Base Commit] → [Apply metamorphic_base_patch] → [Transformed Code]
             → [Apply TRANSFORMED fix.patch] → Tests pass

Actual Failing Flow:
[Base Commit] → [Apply metamorphic_base_patch] → [Transformed Code]
             → [Apply ORIGINAL fix.patch] → FAIL (files don't exist)
```

### Concrete Example (jackson-databind PR 1923)

1. **Metamorphic base patch** renames:
   - `SubTypeValidator.java` → `SubtypeValidator.java`
   - `instance()` → `getInstance()`
   - `validateSubType()` → `validateSubtype()`

2. **Model's prediction** references:
   ```
   diff --git a/.../SubTypeValidator.java b/.../SubTypeValidator.java
   ```

3. **Error in fix-patch-run.log**:
   ```
   error: src/main/java/.../SubTypeValidator.java: No such file or directory
   ```

### Solution Requirements

For metamorphic evaluation to work correctly:

1. **Model must see transformed code**: The agent/model must be presented with the codebase AFTER `metamorphic_base_patch` is applied
2. **Model's predictions must target transformed filenames**: The generated patches must reference the renamed files
3. **OR**: Post-process predictions to transform them using the same metamorphic rules

---

## File Locations

- **Instance implementations**: `multi_swe_bench/harness/repos/{language}/{org}/{repo}.py`
- **Base Image class**: `multi_swe_bench/harness/image.py`
- **Evaluation runner**: `multi_swe_bench/harness/run_evaluation.py`
- **Report generation**: `multi_swe_bench/harness/gen_report.py`
- **Metamorphic helper**: `multi_swe_bench/harness/metamorphic.py`
- **Docker utilities**: `multi_swe_bench/utils/docker_util.py`

---

## Running Evaluation

```bash
python -m multi_swe_bench.harness.run_evaluation \
    --mode evaluation \
    --workdir /path/to/workdir \
    --patch_files /path/to/predictions.jsonl \
    --dataset_files /path/to/dataset.jsonl \
    --repo_dir /path/to/repos \
    --output_dir /path/to/output \
    --log_dir /path/to/logs \
    --force_build true  # Important: rebuilds images with metamorphic patches
```

### Key Arguments

- `--patch_files`: JSONL with model predictions (each line: `{"org": "...", "repo": "...", "number": N, "fix_patch": "..."}`)
- `--dataset_files`: JSONL with benchmark instances (includes `metamorphic_base_patch` in `base` field)
- `--force_build true`: **Critical for metamorphic testing** - ensures images are rebuilt with metamorphic patches applied

---

## MSWE-Agent Integration

### Overview

MSWE-agent is the agent that generates patches by interacting with the codebase in a Docker container. It shares the same image-building infrastructure as multi-swe-bench evaluation.

### MSWE-Agent Workflow

#### Phase 1: Image Building (same as multi-swe-bench)

1. `multirun.py` calls `get_instances()` which calls `prepare_datas()`
2. `prepare_datas()` builds images via `build_images()` if `prebuild=True`
3. During image build, `prepare.sh` runs and applies `metamorphic_base_patch` (if present)
4. The resulting Docker image has the metamorphic transformation **committed**

#### Phase 2: Agent Runtime (`SWEEnv.reset()`)

When the agent starts working on an instance:

1. Container is created from the pre-built image
2. `SWEEnv.reset()` is called in `sweagent/environment/swe_env.py`
3. The agent then interacts with the repository

### Critical Bug Found & Fixed

**The Problem:**

In `swe_env.py:reset()`, there was code that ran:
```python
f"git reset --hard {self.base_commit}"
```

Where `self.base_commit = pr.base.sha` (the **ORIGINAL** commit, not the metamorphic one).

This **UNDID the metamorphic transformation** that was applied during image build!

**The Fix (v2 - Hard Reset + Re-apply):**

Modified `swe_env.py` to always do hard reset, then re-apply the metamorphic patch if it exists:

```python
# Always reset to clean state
for cmd in [
    ...,
    f"git reset --hard {self.base_commit}",
    "git clean -fdxq",
]:
    ...

# Re-apply metamorphic patch if it exists in the image
self.communicate_with_handling(
    input=(
        "if [ -f /home/metamorphic_base.patch ]; then "
        "git apply /home/metamorphic_base.patch && "
        "git add -A && "
        "git commit -m 'Re-apply metamorphic_base_patch transformation'; "
        "fi"
    ),
    ...
)
```

This approach is more robust because:
1. Hard reset guarantees a clean state even if something corrupted the repo
2. `/home/metamorphic_base.patch` is available for reference and debugging
3. Consistent with how other patches are stored in `/home/`

### Key Files (MSWE-Agent)

| File | Purpose |
|------|---------|
| `multirun.py` | Entry point for running agent on multiple instances |
| `sweagent/environment/swe_env.py` | Environment that manages Docker container interaction |
| `sweagent/environment/utils.py` | Utilities including `get_instances()` |
| `multi_swe_bench/harness/build_dataset.py` | Image building logic (shared with multi-swe-bench) |
| `multi_swe_bench/harness/metamorphic.py` | Metamorphic patch application helper |

### Verification

To verify the agent sees metamorphic-transformed code:

1. Build the image: Run MSWE-agent with `--pre_build_all_images=True`
2. Check image state: `docker run -it mockito/mockito:pr-3129 bash` then `cd /home/mockito && git log --oneline -3`
3. You should see a commit with message "Apply `metamorphic_base_patch` transformation to base commit"
4. Verify patch file exists: `ls -la /home/metamorphic_base.patch`

### Important: Docker Image Rebuild Required

After modifying `metamorphic.py` to save patches to `/home/metamorphic_base.patch`, you must **rebuild Docker images** for the changes to take effect. Old images won't have the patch file in `/home/`.
