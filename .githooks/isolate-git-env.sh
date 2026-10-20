# Sourced by the hooks: drop the repo-locating variables git exports to hooks, so nothing the gate runs
# (pytest's temp repos, the smoke's scratch trees) can write to this checkout's .git. Run after `cd` to the top level.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES \
  GIT_COMMON_DIR GIT_NAMESPACE GIT_PREFIX GIT_QUARANTINE_PATH GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT
