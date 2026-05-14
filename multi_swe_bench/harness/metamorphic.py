from multi_swe_bench.harness.pull_request import PullRequest
from multi_swe_bench.utils.logger import get_logger

logger = get_logger("metamorphic")

# Path where the metamorphic patch is stored in the Docker image
# Kept in /home alongside other patches (fix.patch, test.patch) for reference and re-application
METAMORPHIC_PATCH_PATH = "/home/metamorphic_base.patch"


class Metamorphic:
    @staticmethod
    def apply_metamorphic_patch_cmd(
        pr: PullRequest,
        commit_message: str = "Apply `metamorphic_base_patch` transformation to base commit",
    ) -> str:
        """
        Returns git command for **applying and committing** `metamorphic_base_patch`,
        if it's not None in the `pr.base`. Otherwise, returns an empty string (i.e., no-op behavior).

        The patch file is saved to /home/metamorphic_base.patch and kept for:
        1. Reference/debugging purposes
        2. Re-application after git reset --hard (if needed at runtime)
        """
        patch = pr.base.metamorphic_base_patch
        if (patch is not None) and (patch != ""):
            logger.info(f"Found `metamorphic_base_patch` entry: it will be applied to the base commit (`{patch[:150]}...`)")
            return Metamorphic._produce_apply_patch_commands(patch, commit_message)
        return ""

    @staticmethod
    def _produce_apply_patch_commands(patch: str, commit_message: str):
        """
        Returns git command for **applying and committing** `patch` with a given `commit_message`.
        The patch file is kept in /home/metamorphic_base.patch for later re-application.
        """
        return (
            f"cat > {METAMORPHIC_PATCH_PATH} << 'EOF_METAMORPHIC_PATCH'\n"
            f"{patch}\n"
            f"EOF_METAMORPHIC_PATCH\n"
            f"git apply {METAMORPHIC_PATCH_PATH}\n"
            # Note: patch file is NOT deleted - kept for reference and re-application
            f"git add -A && "
            f"git -c user.email='mswe-agent@metamorphic.py' -c user.name='metamorphic-transformation-patch' commit -m '{commit_message}'\n"
        )
