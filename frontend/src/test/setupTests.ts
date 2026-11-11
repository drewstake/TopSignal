import { configure } from "@testing-library/react";

// findBy*/waitFor default to 1 s. Shared CI runners regularly take longer than
// that to render the larger integration flows, which made passing tests flaky.
// This is an upper bound only; passing assertions still resolve immediately.
configure({ asyncUtilTimeout: 5_000 });
