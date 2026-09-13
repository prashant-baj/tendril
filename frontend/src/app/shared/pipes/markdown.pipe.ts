import { Pipe, PipeTransform } from '@angular/core';
import { marked } from 'marked';

/**
 * Renders a chat message's markdown (headings, bold, lists — the orchestrator's replies use
 * light markdown) as real HTML instead of showing raw '###'/'**' characters.
 *
 * Deliberately returns a plain string, not a `SafeHtml` via `DomSanitizer.bypassSecurityTrustHtml`
 * — binding a plain string to `[innerHTML]` still runs through Angular's own sanitizer, which
 * strips `<script>`/event-handler attributes/etc. automatically. Model output is untrusted-enough
 * text to sanitize like any other user-facing content, so bypassing that protection isn't worth
 * the small convenience of allowing arbitrary HTML through.
 */
@Pipe({ name: 'markdown', standalone: true })
export class MarkdownPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) {
      return '';
    }
    return marked.parse(value, { async: false, breaks: true }) as string;
  }
}
