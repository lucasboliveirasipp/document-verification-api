"""Adaptadores de integração com a Gateway.

Este pacote concentra **todo** o conhecimento sobre o transporte HTTP e o
contrato observado da plataforma corporativa (Gateway). Nenhuma outra camada faz
HTTP.

* :mod:`transport` — única classe que conhece HTTP;
* :mod:`service_auth` — token ``client_credentials`` do Azure AD;
* :mod:`execution` — início, polling e leitura do texto de workflows;
* :mod:`text_generation` — workflow "Gerador de Texto" (contrato da Curadoria);
* :mod:`upload` / :mod:`workflow` — caminho B do OCR (File Manager + OCR).

Códigos de workflow, container e URLs são **configuração** e chegam via
:class:`~src.settings.AppSettings`; cada adaptador falha fechado quando falta o
valor de que precisa.
"""
