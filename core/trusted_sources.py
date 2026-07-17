#!/usr/bin/env python3
"""
trusted_sources.py — shared whitelist of trusted news domains.

TRUSTED_DOMAINS (Brazilian, Portuguese-language) is used as a HARD filter
(not a scoring bonus) by the 3 Brazil-market Google News scrapers
(scraper.py, brazil_scraper.py, credit_scraper.py): an article is discarded
unless its source domain is in this set. Not used by china_scraper.py —
none of these domains cover Chinese sources, so applying it there would
zero out that feed entirely.

TRUSTED_DOMAINS_EN (international financial wires/press) is used by
gdelt_scraper.py for its English-language query leg — GDELT deliberately
also pulls non-Brazilian coverage of Brazilian companies (Reuters, Bloomberg,
FT, etc.), which TRUSTED_DOMAINS doesn't cover.

Kept as one shared module (unlike each scraper's usual self-contained
duplication) because multiple scrapers must apply the exact same rule —
duplicating it per-file would risk drift if the list is ever edited.

domain_of() accepts either a full URL (Google News RSS's entry.source.href)
or a bare hostname (GDELT's "domain" field) — both are real call sites.
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

# International financial wires/press — curated, not exhaustive. Restores
# the "international wires" tier that scraper.py/credit_scraper.py used to
# carry locally (as a scoring bonus) before the Brazil whitelist replaced
# it as a hard gate; kept separate from TRUSTED_DOMAINS since these aren't
# Brazilian outlets and shouldn't be treated as PT-language sources.
TRUSTED_DOMAINS_EN = {
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com",
    "marketwatch.com", "barrons.com", "forbes.com", "businessinsider.com",
    "spglobal.com", "moodys.com", "fitchratings.com", "apnews.com",
    "economist.com", "bbc.com",
}

# Reverse lookup for display: GDELT gives us a bare domain, not an outlet
# name (unlike Google News RSS's entry.source.title). Built from the same
# outlet table TRUSTED_DOMAINS was normalized from. Falls back to the bare
# domain (outlet_name()) for anything not in this map, e.g. TRUSTED_DOMAINS_EN
# entries or any future addition — never raises on a miss.
DOMAIN_TO_OUTLET = {
    "acritica.com": "A Crítica",
    "aecweb.com.br": "AECweb",
    "agenciabrasil.ebc.com.br": "Agência Brasil",
    "agenciainfra.com": "Agência iNFRA",
    "agrolink.com.br": "Agrolink",
    "amazoniareal.com.br": "Amazônia Real",
    "apublica.org": "Agência Pública",
    "atarde.com.br": "A Tarde",
    "autodata.com.br": "AutoData",
    "automotivebusiness.com.br": "Automotive Business",
    "baguete.com.br": "Baguete",
    "band.uol.com.br": "BandNews",
    "bbc.com": "BBC News",
    "borainvestir.b3.com.br": "Bora Investir",
    "br.investing.com": "Investing.com Brasil",
    "brasil247.com": "Brasil 247",
    "brasildefato.com.br": "Brasil de Fato",
    "brasilenergia.com.br": "Brasil Energia",
    "brasilmineral.com.br": "Brasil Mineral",
    "braziljournal.com": "Brazil Journal",
    "camara.leg.br": "Agência Câmara",
    "campograndenews.com.br": "Campo Grande News",
    "canalenergia.com.br": "CanalEnergia",
    "canalrural.com.br": "Canal Rural",
    "canaltech.com.br": "Canaltech",
    "capitalaberto.com.br": "Capital Aberto",
    "cartacapital.com.br": "CartaCapital",
    "cbic.org.br": "CBIC Notícias",
    "clickpetroleoegas.com.br": "Click Petróleo e Gás",
    "climainfo.org.br": "ClimaInfo",
    "cnj.jus.br": "Agência CNJ",
    "cnnbrasil.com.br": "CNN Brasil",
    "congressoemfoco.uol.com.br": "Congresso em Foco",
    "conjur.com.br": "ConJur (Consultor Jurídico)",
    "construcaomercado.com.br": "Construção Mercado",
    "consultormunicipal.adv.br": "Consultor Municipal",
    "consumidormoderno.com.br": "Consumidor Moderno",
    "convergenciadigital.com.br": "Convergência Digital",
    "correiobraziliense.com.br": "Correio Braziliense",
    "dc.clicrbs.com.br": "Diário Catarinense",
    "dialogosinstitucionais.com.br": "Diálogos Institucionais",
    "diariodepernambuco.com.br": "Diário de Pernambuco",
    "diariodocentrodomundo.com.br": "Diário do Centro do Mundo (DCM)",
    "distrito.me": "Distrito",
    "dw.com": "DW Brasil",
    "ecodebate.com.br": "EcoDebate",
    "eixos.com.br": "Agência Eixos",
    "em.com.br": "Estado de Minas",
    "energiahoje.com": "EnergiaHoje",
    "estadao.com.br": "Estadão",
    "estradao.estadao.com.br": "Estradão",
    "ethos.org.br": "Instituto Ethos Notícias",
    "exame.com": "Exame",
    "folha.uol.com.br": "Folha de S.Paulo",
    "forbes.com.br": "Forbes Agro",
    "france24.com": "France 24 Brasil",
    "futurodasaude.com.br": "Futuro da Saúde",
    "g1.globo.com": "G1",
    "gauchazh.clicrbs.com.br": "Zero Hora (GZH)",
    "gazetadopovo.com.br": "Gazeta do Povo",
    "globorural.globo.com": "Globo Rural",
    "gov.br": "Agência Gov",
    "guiamaritimo.com.br": "Guia Marítimo",
    "hospitaisbrasil.com.br": "Portal Hospitais Brasil",
    "ibram.org.br": "IBRAM Notícias",
    "iclnoticias.com.br": "ICL Notícias",
    "imoveis.estadao.com.br": "Estadão Imóveis",
    "infoamazonia.org": "InfoAmazonia",
    "infomoney.com.br": "InfoMoney",
    "intercept.com.br": "The Intercept Brasil",
    "jc.ne10.uol.com.br": "Jornal do Commercio",
    "jota.info": "JOTA",
    "jovempan.com.br": "Jovem Pan News",
    "justicaemfoco.com.br": "Justiça em Foco",
    "lexlegal.com.br": "LexLegal Brasil",
    "medscape.com": "Medscape Brasil",
    "megawhat.energy": "MegaWhat",
    "mercadoconsumo.com.br": "Mercado & Consumo",
    "metropoles.com": "Metrópoles",
    "migalhas.com.br": "Migalhas",
    "minasustentavel.com.br": "Mineração & Sustentabilidade",
    "mittechreview.com.br": "MIT Technology Review Brasil",
    "mobiletime.com.br": "Mobile Time",
    "moneyreport.com.br": "Money Rural",
    "moneytimes.com.br": "Money Times",
    "mundologistica.com.br": "MundoLogística",
    "neofeed.com.br": "NeoFeed",
    "news.agrofy.com.br": "Agrofy News Brasil",
    "nexojornal.com.br": "Nexo Jornal",
    "noticias.r7.com": "R7",
    "noticias.uol.com.br": "UOL Notícias",
    "noticiasagricolas.com.br": "Notícias Agrícolas",
    "noticiasdemineracao.com": "Notícias de Mineração Brasil (NMB)",
    "nsctotal.com.br": "NSC Total",
    "oc.eco.br": "Observatório do Clima",
    "oeco.org.br": "((o))eco",
    "oglobo.globo.com": "O Globo",
    "olhardigital.com.br": "Olhar Digital",
    "opovo.com.br": "O Povo",
    "pagina22.com.br": "Página22",
    "petronoticias.com.br": "Petronotícias",
    "piaui.folha.uol.com.br": "Revista Piauí",
    "pipelinevalor.globo.com": "Pipeline Valor",
    "poder360.com.br": "Poder360",
    "portal.fiocruz.br": "Agência Fiocruz de Notícias",
    "portosenavios.com.br": "Portos e Navios",
    "reporterbrasil.org.br": "Repórter Brasil",
    "reset.org.br": "Reset",
    "reuters.com": "Reuters",
    "revistaoeste.com": "Revista Oeste",
    "saecossistema.com.br": "SA+ Ecossistema de Varejo",
    "saudebusiness.com": "Saúde Business",
    "sbtnews.sbt.com.br": "SBT News",
    "secovi.com.br": "Secovi-SP Notícias",
    "senado.leg.br": "Agência Senado",
    "seudinheiro.com": "Seu Dinheiro",
    "startups.com.br": "Startups.com.br",
    "suno.com.br": "Suno Notícias",
    "supervarejo.com.br": "SuperVarejo",
    "tecnoblog.net": "Tecnoblog",
    "teleco.com.br": "Teleco",
    "telesintese.com.br": "Tele.Síntese",
    "teletime.com.br": "Teletime",
    "terra.com.br": "Terra",
    "tiinside.com.br": "TI Inside",
    "tnpetroleo.com.br": "TN Petróleo",
    "trademap.com.br": "TradeMap News",
    "transportemoderno.com.br": "Transporte Moderno",
    "tribunadonorte.com.br": "Tribuna do Norte",
    "umsoplaneta.globo.com": "Um Só Planeta",
    "valor.globo.com": "Valor Econômico",
    # International wires (TRUSTED_DOMAINS_EN)
    "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times",
    "wsj.com": "The Wall Street Journal",
    "cnbc.com": "CNBC",
    "marketwatch.com": "MarketWatch",
    "barrons.com": "Barron's",
    "businessinsider.com": "Business Insider",
    "spglobal.com": "S&P Global",
    "moodys.com": "Moody's",
    "fitchratings.com": "Fitch Ratings",
    "apnews.com": "Associated Press",
    "economist.com": "The Economist",
}


def domain_of(host_or_url: str) -> str:
    """Normalize either a full URL or a bare hostname to a lowercase,
    www-stripped domain. Google News RSS gives full URLs; GDELT gives
    bare hostnames — urlparse only populates netloc for the former unless
    given a scheme-relative "//host" form, so bare strings are coerced."""
    s = (host_or_url or "").strip()
    if not s:
        return ""
    if "//" not in s:
        s = "//" + s
    netloc = urlparse(s).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def is_trusted(host_or_url: str, domains: set = None) -> bool:
    return domain_of(host_or_url) in (domains if domains is not None else TRUSTED_DOMAINS)


def outlet_name(host_or_url: str) -> str:
    d = domain_of(host_or_url)
    return DOMAIN_TO_OUTLET.get(d, d)
