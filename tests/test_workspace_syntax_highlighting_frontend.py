"""Literal, bounded syntax colors for source previews and chat code blocks."""
import unittest
from tests import test_workspace_rich_content_frontend as rich_content


@unittest.skipUnless(rich_content.NODE, "Node.js is required for syntax rendering checks")
class WorkspaceSyntaxHighlightingFrontendTests(unittest.TestCase):
    run_case = rich_content.WorkspaceRichContentFrontendTests.run_case

    def test_html_and_xml_mark_tags_attributes_strings_and_comments_without_rendering_them(self):
        self.run_case(r"""(()=>{
          const source='<!doctype html>\n<!-- 한국어 주석 -->\n<div class="card" data-note="&lt;literal&gt;">안내 &amp; 원문</div>';
          R.code(parent,source,'html');
          assert.equal(tag(parent,'code')[0].textContent,source);
          assert.ok(cls(parent,'rich-token-tag').some(n=>n.textContent.includes('div')));
          assert.ok(cls(parent,'rich-token-attribute').some(n=>n.textContent.includes('class')));
          assert.ok(cls(parent,'rich-token-string').some(n=>n.textContent.includes('card')));
          assert.ok(cls(parent,'rich-token-comment').some(n=>n.textContent.includes('한국어 주석')));
          assert.equal(tag(parent,'div').filter(n=>n.className==='card').length,0);
          const xml=new Element();xml.root=true;
          R.code(xml,'<?xml version="1.0"?><item enabled="true">내용</item>','xml');
          assert.ok(cls(xml,'rich-token-tag').some(n=>n.textContent.includes('item')));
          assert.ok(cls(xml,'rich-token-attribute').some(n=>n.textContent.includes('enabled')));
          assert.equal(apiCalls.length,0);assert.equal(opens.length,0);
        })()""")

    def test_html_style_and_script_blocks_use_their_respective_syntax(self):
        self.run_case(r"""(()=>{
          const source='<style>\n/* dashboard */\n.card { color: #ffffff; padding: 12px; content: "보고서"; }\n</style>\n<script>\nconst title = "요약";\nfunction total(value) { return value + 3; }\nconst result = total(2); // calculate\n</script>';
          R.code(parent,source,'html',{lineNumbers:true});
          assert.equal(tag(parent,'code')[0].textContent,source);
          assert.ok(cls(parent,'rich-token-selector').some(n=>n.textContent.includes('.card')));
          assert.ok(cls(parent,'rich-token-property').some(n=>n.textContent.includes('color')));
          assert.ok(cls(parent,'rich-token-number').some(n=>n.textContent.includes('12')));
          assert.ok(cls(parent,'rich-token-keyword').some(n=>n.textContent==='const'));
          assert.ok(cls(parent,'rich-token-function').some(n=>n.textContent==='total'));
          assert.ok(cls(parent,'rich-token-string').some(n=>n.textContent.includes('요약')));
          assert.ok(cls(parent,'rich-token-comment').some(n=>n.textContent.includes('calculate')));
          assert.equal(tag(parent,'style').length,0);assert.equal(tag(parent,'script').length,0);
        })()""")

    def test_windows_path_attribute_does_not_swallow_following_html_tags(self):
        self.run_case(r"""(()=>{
          const source=String.raw`<div data-path="C:\reports\"><span title="next">안내</span></div>`;
          R.code(parent,source,'html');
          assert.equal(tag(parent,'code')[0].textContent,source);
          assert.ok(cls(parent,'rich-token-tag').some(n=>n.textContent.includes('span')));
          assert.ok(cls(parent,'rich-token-attribute').some(n=>n.textContent==='title'));
          assert.ok(cls(parent,'rich-token-string').some(n=>n.textContent==='"next"'));
        })()""")

    def test_standalone_css_preserves_custom_properties_and_literally_shows_unsafe_text(self):
        self.run_case(r"""(()=>{
          const source=':root { --card: #192432; }\n.card:hover { background: var(--card); width: calc(100% - 12px); content: "<img onerror=evil()>"; }';
          R.code(parent,source,'css');
          assert.equal(tag(parent,'code')[0].textContent,source);
          assert.ok(cls(parent,'rich-token-property').some(n=>n.textContent.includes('--card')));
          assert.ok(cls(parent,'rich-token-selector').some(n=>n.textContent.includes('.card')));
          assert.ok(cls(parent,'rich-token-function').some(n=>n.textContent==='calc'));
          assert.equal(tag(parent,'img').length,0);assert.equal(apiCalls.length,0);
        })()""")

    def test_highlighted_html_copy_keeps_original_entities_tabs_and_line_endings(self):
        self.run_case(r"""(async()=>{
          const source='<html lang="ko">\r\n\t<!-- &amp; 그대로 -->\r\n<img src="missing.png" onerror="evil()">\r\n<script>globalThis.evil = true;</script>\r\n</html>\r\n';
          const card=R.code(parent,source,'html',{lineNumbers:true});
          assert.equal(tag(card,'code')[0].textContent,source.replace(/\r\n/g,'\n'));
          await tag(card,'button')[0].onclick();assert.equal(readClipboard(),source);
          assert.equal(tag(card,'script').length,0);assert.equal(tag(card,'img').length,0);
          assert.equal(apiCalls.length,0);assert.equal(opens.length,0);assert.equal(globalThis.evil,undefined);
          const fold=tag(card,'button').find(n=>n.title==='코드 접기');fold.onclick();
          assert.equal(tag(card,'pre')[0].hidden,true);fold.onclick();assert.equal(tag(card,'pre')[0].hidden,false);
        })()""")

    def test_page_501_retains_html_style_and_multiline_comment_context(self):
        self.run_case(r"""(()=>{
          Element.prototype.focus=function(){this.focused=true;};
          const lines=['<style>',...Array(498).fill(''),'/* comment starts','comment continues */','.row { color: red; padding: 2px; }','</style>'];
          const card=R.code(parent,lines.join('\n'),'html',{lineNumbers:true});
          tag(card,'button').find(n=>n.title==='다음 500줄 보기').onclick();
          assert.equal(cls(card,'source-line-numbers')[0].attributes['data-numbers'].split('\n')[0],'501');
          assert.equal(tag(card,'code')[0].textContent,lines.slice(500).join('\n'));
          assert.ok(cls(card,'rich-token-comment').some(n=>n.textContent.includes('comment continues')));
          assert.ok(cls(card,'rich-token-property').some(n=>n.textContent.includes('color')));
          assert.ok(cls(card,'rich-token-selector').some(n=>n.textContent.includes('.row')));
        })()""")

    def test_page_501_retains_script_context_and_later_keywords(self):
        self.run_case(r"""(()=>{
          Element.prototype.focus=function(){this.focused=true;};
          const lines=['<script>',...Array(498).fill(''),'/* comment starts','comment continues */','const answer = helper(2);','</script>'];
          const card=R.code(parent,lines.join('\n'),'html',{lineNumbers:true});
          tag(card,'button').find(n=>n.title==='다음 500줄 보기').onclick();
          assert.equal(tag(card,'code')[0].textContent,lines.slice(500).join('\n'));
          assert.ok(cls(card,'rich-token-comment').some(n=>n.textContent.includes('comment continues')));
          assert.ok(cls(card,'rich-token-keyword').some(n=>n.textContent==='const'));
          assert.ok(cls(card,'rich-token-function').some(n=>n.textContent==='helper'));
        })()""")

    def test_minified_html_is_bounded_and_never_loses_uncolored_remainder(self):
        self.run_case(r"""(()=>{
          const source='<div class="card" data-i="1">value</div>'.repeat(3000).slice(0,100000);
          R.code(parent,source,'html');
          assert.equal(tag(parent,'code')[0].textContent,source);
          const colored=all(parent,n=>String(n.className||'').startsWith('rich-token-'));
          assert.ok(colored.length>0&&colored.length<=2000);
          assert.ok(all(parent,()=>true).length<5000);
          const second=new Element();second.root=true;
          const incomplete='<div title="'+ '<>&'.repeat(40000).slice(0,99988);
          R.code(second,incomplete,'html');
          assert.equal(tag(second,'code')[0].textContent,incomplete.slice(0,100000));
          assert.equal(tag(second,'script').length,0);assert.equal(apiCalls.length,0);
        })()""")

    def test_unknown_language_stays_literal_and_shared_highlight_budget_is_respected(self):
        self.run_case(r"""(()=>{
          const source='<div class="card">const unknown = 1;</div>';
          R.code(parent,source,'unrecognized-format');
          assert.equal(tag(parent,'code')[0].textContent,source);
          assert.equal(all(parent,n=>String(n.className||'').startsWith('rich-token-')).length,0);
          const shared={remaining:8},one=new Element(),two=new Element();one.root=two.root=true;
          R.code(one,source,'html',{highlightBudget:shared});R.code(two,source,'html',{highlightBudget:shared});
          assert.equal(tag(one,'code')[0].textContent,source);assert.equal(tag(two,'code')[0].textContent,source);
          const count=node=>all(node,n=>String(n.className||'').startsWith('rich-token-')).length;
          assert.ok(count(one)>0);assert.ok(count(one)+count(two)<=8);assert.ok(shared.remaining>=0);
        })()""")
