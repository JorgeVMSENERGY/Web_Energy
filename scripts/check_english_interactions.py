"""Local browser interaction checks. Does not submit or upload any form."""
import json
import time
from check_english_browser import Browser


def main():
    browser = Browser()
    try:
        browser.navigate('en/index.html')
        browser.evaluate('localStorage.removeItem("vms_consent_v1"); vmsCookies(); document.querySelector("[data-ck=config]").click()')
        time.sleep(.1)
        cookie = browser.evaluate('document.getElementById("vmsCookies").innerText')
        assert 'Save preferences' in cookie and 'Necessary (always enabled)' in cookie, cookie
        browser.evaluate('document.querySelector("[data-ck=necesarias]").click()')
        consent = browser.evaluate('JSON.parse(localStorage.getItem("vms_consent_v1"))')
        assert consent['analitica'] is False and consent['mercadotecnia'] is False
        print('Cookie settings and consent values: OK', flush=True)

        browser.navigate('en/catalogo-proyectos.html')
        browser.evaluate('document.querySelector("[data-filter-tipo=Automatización]").click()')
        time.sleep(.1)
        label = browser.evaluate('document.querySelector("[data-filter-tipo=Automatización]").textContent')
        assert 'Automation' in label, label
        assert browser.evaluate('document.getElementById("cpGrid").children.length') > 0
        browser.evaluate('document.getElementById("btnList").click()')
        assert browser.evaluate('document.getElementById("btnList").getAttribute("aria-pressed")') == 'true'
        print('Project filters and list view: OK', flush=True)

        browser.navigate('en/carreras.html')
        browser.evaluate('document.querySelector("[data-filter=proyecto]").click()')
        time.sleep(.1)
        assert browser.evaluate('document.querySelector("[data-filter=proyecto]").classList.contains("is-active")')
        print('Career filters: OK', flush=True)

        browser.navigate('en/calculadora-ahorro-bess-gdmth.html')
        browser.evaluate('document.getElementById("demandaMax").value=500; document.getElementById("demandaObj").value=350; document.getElementById("horasPico").value=4; document.getElementById("cargoDemanda").value=420; nextStep(1); nextStep(2); calcular()')
        time.sleep(.2)
        assert browser.evaluate('document.getElementById("resultsPanel").classList.contains("is-visible")')
        assert not browser.errors, browser.errors
        print('BESS calculator result: OK', flush=True)

        browser.navigate('catalogo-proyectos.html')
        switch = browser.evaluate('document.querySelector(".lang-switch").href')
        assert switch.endswith('/en/catalogo-proyectos.html'), switch
        browser.navigate('proyectos-vms.html')
        switch = browser.evaluate('document.querySelector(".lang-switch").href')
        assert switch.endswith('/en/proyectos-vms.html'), switch
        print('Staging-owned native pages retain language navigation: OK', flush=True)

        browser.call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
        browser.navigate('en/index.html?source=qa#inicio')
        browser.evaluate('document.getElementById("menuToggle").click()')
        assert browser.evaluate('document.getElementById("menuToggle").getAttribute("aria-expanded")') == 'true'
        assert browser.evaluate('document.querySelector(".lang-switch").href').endswith('/index.html?source=qa#inicio')
        assert not browser.evaluate('document.documentElement.scrollWidth > innerWidth + 2')
        print('Mobile menu and counterpart query/anchor: OK', flush=True)
    finally:
        browser.close()


if __name__ == '__main__':
    main()
