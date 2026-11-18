// How a goal gets its issues, in the words the New goal form asks it in. The service calls this the goal's
// modality (fix, feature); a page never does.
export const ISSUE_SOURCES: Record<string, string> = {
  fix: "Issues that exist",
  feature: "Planned from the description",
};

export const issueSourceLabel = (modality: string): string => ISSUE_SOURCES[modality] ?? modality;
