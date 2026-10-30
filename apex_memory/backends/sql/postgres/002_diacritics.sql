-- Match FTS5 unicode61 remove_diacritics=1 without requiring an extension.
-- Translation derived from SQLite 3.53.1 fts5vocab, comparing remove_diacritics=0 and 1
-- for BMP letters/marks between two ASCII letters. unicode61's diacritic table is BMP-only.
-- Keep its historical exception for precomposed letters with multiple diacritics (e.g. ộ),
-- and leave ligatures intact. Combining marks removed by unicode61 are last, for deletion.
CREATE FUNCTION unicode61_fold(text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT translate($1, 'ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÑÒÓÔÕÖÙÚÛÜÝàáâãäåçèéêëìíîïñòóôõöùúûüýÿĀāĂăĄąĆćĈĉĊċČčĎďĒēĔĕĖėĘęĚěĜĝĞğĠġĢģĤĥĨĩĪīĬĭĮįİĴĵĶķĹĺĻļĽľŃńŅņŇňŌōŎŏŐőŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžƠơƯưǍǎǏǐǑǒǓǔǦǧǨǩǪǫǰǴǵǸǹȀȁȂȃȄȅȆȇȈȉȊȋȌȍȎȏȐȑȒȓȔȕȖȗȘșȚțȞȟȦȧȨȩȮȯȲȳḀḁḂḃḄḅḆḇḊḋḌḍḎḏḐḑḒḓḘḙḚḛḞḟḠḡḢḣḤḥḦḧḨḩḪḫḬḭḰḱḲḳḴḵḶḷḺḻḼḽḾḿṀṁṂṃṄṅṆṇṈṉṊṋṔṕṖṗṘṙṚṛṞṟṠṡṢṣṪṫṬṭṮṯṰṱṲṳṴṵṶṷṼṽṾṿẀẁẂẃẄẅẆẇẈẉẊẋẌẍẎẏẐẑẒẓẔẕẖẗẘẙẛẠạẢảẸẹẺẻẼẽỈỉỊịỌọỎỏỤụỦủỲỳỴỵỶỷỸỹẠ̧̨̛̤̥̦̭̮̰̱̊̀́̂̃̄̆̇̈̉̊̋̌̏̑', 'aaaaaaceeeeiiiinooooouuuuyaaaaaaceeeeiiiinooooouuuuyyaaaaaaccccccccddeeeeeeeeeegggggggghhiiiiiiiiijjkkllllllnnnnnnoooooorrrrrrssssssssttttuuuuuuuuuuuuwwyyyzzzzzzoouuaaiioouuggkkoojggnnaaaaeeeeiiiioooorrrruuuusstthhaaeeooyyaabbbbbbddddddddddeeeeffgghhhhhhhhhhiikkkkkkllllllmmmmmmnnnnnnnnpppprrrrrrssssttttttttuuuuuuvvvvwwwwwwwwwwxxxxyyzzzzzzhtwysaaaaeeeeeeiiiioooouuuuyyyyyyyya')
$$;

-- Existing stores need rebuilt indexes, not just normalized queries.
DROP INDEX notes_fts;
DROP INDEX sections_fts;
DROP INDEX events_fts;
CREATE INDEX notes_fts ON notes USING gin ((setweight(to_tsvector('simple', unicode61_fold(coalesce(title, ''))), 'A') || setweight(to_tsvector('simple', unicode61_fold(coalesce(summary, ''))), 'B')));
CREATE INDEX sections_fts ON sections USING gin ((setweight(to_tsvector('simple', unicode61_fold(coalesce(heading, ''))), 'A') || setweight(to_tsvector('simple', unicode61_fold(body)), 'B')));
CREATE INDEX events_fts ON events USING gin ((setweight(to_tsvector('simple', unicode61_fold(op)), 'A') || setweight(to_tsvector('simple', unicode61_fold(coalesce(body, ''))), 'B')));
