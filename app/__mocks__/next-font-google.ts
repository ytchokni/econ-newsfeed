function makeFontMock() {
  return {
    className: "mock-font",
    variable: "--mock-font",
    style: { fontFamily: "mock" },
  };
}

export function Source_Serif_4() { return makeFontMock(); }
export function DM_Sans() { return makeFontMock(); }
