# External Skill CI

Every external Skill repository should run offline CI before it is considered
installable.

The template workflow tests Python 3.12 and 3.14 and performs:

1. editable package install;
2. dependency consistency check;
3. SDK conformance test;
4. normal offline Skill tests.

CI must not require:

- a Discord token;
- a production guild;
- GamerHQ production data;
- VPS access;
- private `.env` files.

A live Discord test is a separate acceptance step after reviewed deployment.

For released Skills, pin the SDK dependency to a reviewed SDK version or commit.
Do not test released builds only against a moving branch.
