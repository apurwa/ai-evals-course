# Title of the note

One or two sentences that say what this note is about. This first paragraph
becomes the summary on the reference home and the lede on the page, so make it
stand on its own.

## A section

Write in plain markdown. Headings, **bold**, *italic*, `inline code`, links like
[Langfuse](https://github.com/langfuse/langfuse), and lists all render:

- a point
- another point
  - and a nested one

Fenced code blocks work too:

```python
print("hello, evals")
```

> A blockquote, for a claim worth setting apart.

When you are done, run `make reference` to regenerate the offline HTML, then
commit both the markdown and the generated files.
