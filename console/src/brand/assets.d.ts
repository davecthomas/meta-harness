// How the brand's artwork is imported: as its text, to be drawn and moved, or as an address for an image.
declare module "*.svg?raw" {
  const text: string;
  export default text;
}
declare module "*.svg" {
  const url: string;
  export default url;
}
