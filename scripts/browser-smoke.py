#!/usr/bin/env python3
"""Real Chromium acceptance checks. Run separately from dependency-light pytest.

Requires Python Playwright plus Chromium. The password is read from
AIRSPACE_TEST_PASSWORD and never printed. Test a deliberately configured demo
instance rather than a live receiver. BASE_URL defaults to http://localhost:8088.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'artifacts'/'screenshots'
BASE = os.environ.get('BASE_URL','http://localhost:8088').rstrip('/')
PASSWORD = os.environ.get('AIRSPACE_TEST_PASSWORD')


def navigation(page, name):
    route = {'Overview':'home','Explore':'explore','Compare':'compare','Patterns':'patterns','Database':'data','Settings':'settings'}[name]
    page.get_by_role('navigation',name='Main navigation').locator(f'a[href="#{route}"]').click()


def no_overflow(page, label):
    sizes=page.evaluate('({viewport:innerWidth, document:document.documentElement.scrollWidth, body:document.body.scrollWidth})')
    assert max(sizes['document'],sizes['body']) <= sizes['viewport']+1, (label,sizes)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    errors=[]
    failed_responses=[]
    checked=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=1)
        page=context.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('response',lambda r:failed_responses.append({'url':r.url,'status':r.status,'body':r.text()[:1000]}) if '/api/' in r.url and r.status>=400 else None)
        page.goto(BASE,wait_until='domcontentloaded')
        expect(page.get_by_role('heading',name='Today at a glance')).to_be_visible(timeout=30000)
        expect(page.locator('.stat-value').first).not_to_have_text('—',timeout=30000)
        expect(page.locator('canvas').first).to_be_visible(timeout=30000)
        expect(page.get_by_text('DEMO WORKSPACE',exact=True)).to_be_visible()
        assert page.locator('.service-card').count()>=6
        no_overflow(page,'desktop home')
        page.screenshot(path=str(OUT/'desktop-home.png'),full_page=True)
        checked.append('desktop home/source/summary/charts/service links')

        navigation(page,'Explore')
        expect(page.get_by_role('heading',name='Explore your airspace.')).to_be_visible()
        page.get_by_label(re.compile('^Time window')).select_option('46')
        page.get_by_label(re.compile('^Group by')).select_option('hour')
        page.locator('.metric-switch').get_by_role('button',name='Observations',exact=True).click()
        with page.expect_response(lambda r:'/api/analytics?' in r.url and parse_qs(urlsplit(r.url).query).get('metric')==['observations']) as response:
            page.get_by_role('button',name='Apply view').click()
        analysis=response.value.json()
        assert response.value.ok,analysis
        assert analysis['end']-analysis['start']==46*3600
        assert analysis['group']=='hour' and analysis['metric']=='observations'
        assert analysis['total']>0 and len(analysis['series'])>=46
        expect(page.locator('canvas').first).to_be_visible()
        page.get_by_label('Chart style').select_option('bar')
        page.locator('.series-details').first.locator('summary').click()
        expect(page.locator('.series-details').first.locator('tbody tr')).to_have_count(len(analysis['series']))
        assert '/api/analytics/export?' in page.get_by_role('link',name='Export chart data CSV').get_attribute('href')
        no_overflow(page,'desktop explore')
        page.screenshot(path=str(OUT/'desktop-explore.png'),full_page=True)
        checked.append('46-hour hourly observations/chart style/table/export')

        navigation(page,'Compare')
        with page.expect_response(lambda r:'/api/compare?' in r.url) as response:
            page.get_by_role('button',name='Today vs yesterday · same elapsed time',exact=True).click()
        comparison=response.value.json()
        assert response.value.ok,comparison
        assert abs((comparison['a']['end']-comparison['a']['start'])-(comparison['b']['end']-comparison['b']['start']))<=1,comparison
        expect(page.get_by_role('heading',name='Period over period')).to_be_visible(timeout=30000)
        no_overflow(page,'desktop compare')
        checked.append('today/yesterday matched elapsed durations')

        navigation(page,'Patterns')
        expect(page.get_by_role('heading',name='Hour by hour',exact=True)).to_be_visible(timeout=30000)
        expect(page.locator('canvas')).to_have_count(2,timeout=30000)
        no_overflow(page,'desktop patterns')
        page.screenshot(path=str(OUT/'desktop-patterns.png'),full_page=True)
        checked.append('weekday patterns/hour heatmap')

        navigation(page,'Database')
        expect(page.locator('.raw-table tbody tr').first).to_be_visible(timeout=30000)
        page.get_by_label('Search records').fill('A320')
        with page.expect_response(lambda r:'/api/data/rows?' in r.url and parse_qs(urlsplit(r.url).query).get('search')==['A320']) as response:
            page.get_by_role('button',name='Search',exact=True).click()
        rows=response.value.json()
        assert response.value.ok and rows['total']>0 and rows['rows']
        expect(page.locator('.raw-table tbody tr')).to_have_count(min(rows['limit'],rows['total']))
        page.get_by_role('button',name='View schema',exact=True).click()
        expect(page.locator('.schema-grid code').first).to_be_visible()
        page.locator('.raw-table tbody tr').first.get_by_role('button').first.click()
        expect(page.get_by_role('dialog')).to_be_visible()
        expect(page.get_by_role('heading',name='Recorded history in selected window')).to_be_visible()
        page.get_by_role('button',name='Close dialog').click()
        no_overflow(page,'desktop database')
        checked.append('database literal search/pagination/schema/aircraft detail')

        navigation(page,'Settings')
        expect(page.get_by_role('heading',name='Workspace settings.')).to_be_visible()
        expect(page.get_by_role('heading',name='A fresh perspective, without the terminal')).to_be_visible()
        if PASSWORD:
            page.get_by_role('button',name='Admin',exact=True).click()
            page.get_by_label('Administrator password').fill(PASSWORD)
            with page.expect_response(lambda r:'/api/auth/login' in r.url) as response:
                page.get_by_role('button',name='Unlock settings',exact=True).click()
            assert response.value.ok,'administrator login failed'
            expect(page.get_by_role('button',name='Lock settings',exact=True)).to_be_visible()
            page.get_by_label(re.compile('^Appearance')).select_option('light')
            with page.expect_response(lambda r:'/api/settings' in r.url and r.request.method=='PUT') as response:
                page.get_by_role('button',name='Save changes',exact=True).click()
            assert response.value.ok,'settings save failed'
            expect(page.locator('html')).to_have_attribute('data-theme','light')
            page.reload(wait_until='domcontentloaded')
            expect(page.get_by_label(re.compile('^Appearance'))).to_have_value('light',timeout=30000)
            page.get_by_role('button',name='Add shortcut',exact=True).click()
            editor=page.locator('.editor-row').last
            editor.get_by_label('Name',exact=True).fill('Browser smoke shortcut')
            editor.get_by_label('URL · http or https',exact=True).fill('http://192.168.1.187:8888')
            editor.get_by_label('Description',exact=True).fill('Temporary browser acceptance check')
            with page.expect_response(lambda r:'/api/settings' in r.url and r.request.method=='PUT') as response:
                page.get_by_role('button',name='Save changes',exact=True).click()
            assert response.value.ok
            navigation(page,'Overview')
            shortcut=page.locator('.service-card').filter(has_text='Browser smoke shortcut')
            expect(shortcut).to_be_visible(timeout=30000)
            assert shortcut.get_attribute('href')=='http://192.168.1.187:8888'
            navigation(page,'Settings')
            page.get_by_role('button',name='Remove Browser smoke shortcut',exact=True).click()
            page.get_by_label(re.compile('^Appearance')).select_option('dark')
            with page.expect_response(lambda r:'/api/settings' in r.url and r.request.method=='PUT') as response:
                page.get_by_role('button',name='Save changes',exact=True).click()
            assert response.value.ok
            checked.append('administrator login/CSRF/theme persistence/custom shortcut lifecycle')
        else:
            checked.append('administrator UI mutation UNRUN: AIRSPACE_TEST_PASSWORD absent')
        no_overflow(page,'desktop settings')
        page.screenshot(path=str(OUT/'desktop-settings.png'),full_page=True)

        mobile=browser.new_context(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
        phone=mobile.new_page()
        phone.on('pageerror',lambda error:errors.append(str(error)))
        phone.goto(BASE,wait_until='domcontentloaded')
        expect(phone.get_by_role('heading',name='Today at a glance')).to_be_visible(timeout=30000)
        expect(phone.locator('.stat-value').first).not_to_have_text('—',timeout=30000)
        no_overflow(phone,'mobile home')
        phone.screenshot(path=str(OUT/'mobile-home.png'),full_page=True)
        for name in ['Explore','Compare','Patterns','Database','Settings']:
            navigation(phone,name)
            expect(phone.locator('.page-heading h1')).to_be_visible(timeout=30000)
            phone.wait_for_timeout(400)
            no_overflow(phone,'mobile '+name)
        phone.screenshot(path=str(OUT/'mobile-settings.png'),full_page=True)
        checked.append('390px touch viewport/all six routes/no horizontal overflow')
        assert not errors,errors
        assert not failed_responses,failed_responses
        browser.close()
    print(json.dumps({'passed':checked,'page_errors':errors,'failed_api_requests':failed_responses,'screenshots':str(OUT)},indent=2))


if __name__=='__main__':
    main()
