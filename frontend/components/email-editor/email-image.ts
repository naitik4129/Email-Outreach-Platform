import { mergeAttributes, Node } from "@tiptap/core";

// Inline image for email bodies. Uploaded images are referenced from the stored
// HTML as <img src="cid:CONTENT_ID"> (the file travels inside the message), but
// a browser cannot display cid: URLs. While editing, the node therefore carries
// a temporary display URL in `src` and the content id in `data-cid`;
// editorHtmlToCid() (email-html.ts) turns it back before anything is saved.
export const EmailImage = Node.create({
  name: "image",
  group: "inline",
  inline: true,
  atom: true,
  draggable: true,

  addAttributes() {
    return {
      src: { default: null },
      alt: { default: "" },
      width: { default: null },
      dataCid: {
        default: null,
        parseHTML: (element) => element.getAttribute("data-cid"),
        renderHTML: (attributes) =>
          attributes.dataCid ? { "data-cid": attributes.dataCid as string } : {},
      },
    };
  },

  parseHTML() {
    return [{ tag: "img[src]" }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["img", mergeAttributes(HTMLAttributes)];
  },
});
