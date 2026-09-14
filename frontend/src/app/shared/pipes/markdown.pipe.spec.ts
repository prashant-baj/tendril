import { MarkdownPipe } from './markdown.pipe';

describe('MarkdownPipe', () => {
  let pipe: MarkdownPipe;

  beforeEach(() => {
    pipe = new MarkdownPipe();
  });

  it('renders a heading and bold text as real HTML, not raw markdown characters', () => {
    const html = pipe.transform('### Fertilizer\n**Hold off** for now.');
    expect(html).toContain('<h3');
    expect(html).toContain('<strong>Hold off</strong>');
    expect(html).not.toContain('###');
  });

  it('renders a bullet list', () => {
    const html = pipe.transform('- water deeply\n- mulch the pots');
    expect(html).toContain('<ul>');
    expect(html).toContain('<li>water deeply</li>');
  });

  it('returns an empty string for null/undefined/empty input', () => {
    expect(pipe.transform(null)).toBe('');
    expect(pipe.transform(undefined)).toBe('');
    expect(pipe.transform('')).toBe('');
  });
});
