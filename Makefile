.PHONY: dump test
KEY=$(file < .OpenAPIKey)
CSVNAME=$(addsuffix .csv,$(notdir $(1)))
affaires-sensibles.csv:
	$(eval URL=https://www.radiofrance.fr/franceinter/podcasts/affaires-sensibles)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
lsd-la-serie-documentaire.csv:
	$(eval URL=https://www.radiofrance.fr/franceculture/podcasts/lsd-la-serie-documentaire)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
les-nuits-de-france-culture.csv:
	$(eval URL=https://www.radiofrance.fr/franceculture/podcasts/les-nuits-de-france-culture)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
les-pieds-sur-terre.csv:
	$(eval URL=https://www.radiofrance.fr/franceculture/podcasts/les-pieds-sur-terre)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
le-cours-de-l-histoire.csv:
	$(eval URL=https://www.radiofrance.fr/franceculture/podcasts/le-cours-de-l-histoire)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
%.html: %.csv
	echo '<link rel="stylesheet" href="index.css">' > $@
	python csv2html.py < $< >> $@
test:
	@echo $(KEY)
	$(eval URL=https://www.radiofrance.fr/franceinter/podcasts/affaires-sensibles)
	@echo $(URL)
	@echo $(call CSVNAME,$(URL))
