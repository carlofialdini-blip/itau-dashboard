#!/usr/bin/env python3
"""
trusted_sources.py — shared whitelist of trusted news domains.

TRUSTED_DOMAINS (Brazilian, Portuguese-language) is used as a HARD filter
(not a scoring bonus) by the 3 Brazil-market Google News scrapers
(scraper.py, brazil_scraper.py, credit_scraper.py): an article is discarded
unless its source domain is in this set. Not used by china_scraper.py —
none of these domains cover Chinese sources, so applying it there would
zero out that feed entirely.

Kept as one shared module (unlike each scraper's usual self-contained
duplication) because multiple scrapers must apply the exact same rule —
duplicating it per-file would risk drift if the list is ever edited.
"""

from urllib.parse import urlparse

TRUSTED_DOMAINS = {
    "acritica.com", "aecweb.com.br", "agenciabrasil.ebc.com.br",
    "agenciainfra.com", "agrolink.com.br", "amazoniareal.com.br",
    "apublica.org", "atarde.com.br", "autodata.com.br",
    "automotivebusiness.com.br", "baguete.com.br", "band.uol.com.br",
    "bbc.com", "borainvestir.b3.com.br", "br.investing.com",
    "brasil247.com", "brasildefato.com.br", "brasilenergia.com.br",
    "brasilmineral.com.br", "braziljournal.com", "camara.leg.br",
    "campograndenews.com.br", "canalenergia.com.br", "canalrural.com.br",
    "canaltech.com.br", "capitalaberto.com.br", "cartacapital.com.br",
    "cbic.org.br", "clickpetroleoegas.com.br", "climainfo.org.br",
    "cnj.jus.br", "cnnbrasil.com.br", "congressoemfoco.uol.com.br",
    "conjur.com.br", "construcaomercado.com.br",
    "consultormunicipal.adv.br", "consumidormoderno.com.br",
    "convergenciadigital.com.br", "correiobraziliense.com.br",
    "dc.clicrbs.com.br", "dialogosinstitucionais.com.br",
    "diariodepernambuco.com.br", "diariodocentrodomundo.com.br",
    "distrito.me", "dw.com", "ecodebate.com.br", "eixos.com.br",
    "em.com.br", "energiahoje.com", "estadao.com.br",
    "estradao.estadao.com.br", "ethos.org.br", "exame.com",
    "folha.uol.com.br", "forbes.com.br", "france24.com",
    "futurodasaude.com.br", "g1.globo.com", "gauchazh.clicrbs.com.br",
    "gazetadopovo.com.br", "globorural.globo.com", "gov.br",
    "guiamaritimo.com.br", "hospitaisbrasil.com.br", "ibram.org.br",
    "iclnoticias.com.br", "imoveis.estadao.com.br", "infoamazonia.org",
    "infomoney.com.br", "intercept.com.br", "jc.ne10.uol.com.br",
    "jota.info", "jovempan.com.br", "justicaemfoco.com.br",
    "lexlegal.com.br", "medscape.com", "megawhat.energy",
    "mercadoconsumo.com.br", "metropoles.com", "migalhas.com.br",
    "minasustentavel.com.br", "mittechreview.com.br", "mobiletime.com.br",
    "moneyreport.com.br", "moneytimes.com.br", "mundologistica.com.br",
    "neofeed.com.br", "news.agrofy.com.br", "nexojornal.com.br",
    "noticias.r7.com", "noticias.uol.com.br", "noticiasagricolas.com.br",
    "noticiasdemineracao.com", "nsctotal.com.br", "oc.eco.br",
    "oeco.org.br", "oglobo.globo.com", "olhardigital.com.br",
    "opovo.com.br", "pagina22.com.br", "petronoticias.com.br",
    "piaui.folha.uol.com.br", "pipelinevalor.globo.com", "poder360.com.br",
    "portal.fiocruz.br", "portosenavios.com.br", "reporterbrasil.org.br",
    "reset.org.br", "reuters.com", "revistaoeste.com",
    "saecossistema.com.br", "saudebusiness.com", "sbtnews.sbt.com.br",
    "secovi.com.br", "senado.leg.br", "seudinheiro.com",
    "startups.com.br", "suno.com.br", "supervarejo.com.br",
    "tecnoblog.net", "teleco.com.br", "telesintese.com.br",
    "teletime.com.br", "terra.com.br", "tiinside.com.br",
    "tnpetroleo.com.br", "trademap.com.br", "transportemoderno.com.br",
    "tribunadonorte.com.br", "umsoplaneta.globo.com", "valor.globo.com",
}


def domain_of(host_or_url: str) -> str:
    """Normalize a full URL to a lowercase, www-stripped domain. Google
    News RSS gives full URLs (entry.source.href) — that's the only real
    call site."""
    s = (host_or_url or "").strip()
    if not s:
        return ""
    if "//" not in s:
        s = "//" + s
    netloc = urlparse(s).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def is_trusted(host_or_url: str) -> bool:
    return domain_of(host_or_url) in TRUSTED_DOMAINS
